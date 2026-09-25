# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A block holding its function's only binding of a name the function reads elsewhere stays.

Python makes a name local to a function when any of the function's code
binds it, so a read before the binding raises ``UnboundLocalError`` and a
nested scope reads the function's variable. Moving the only binding into a
helper made the name non-local: the round-4 audit's P1-04, where
``return ("empty", total)`` before the block returned the module's ``total``.

``scope_moving_names`` names what moving a block would do that to. The table
below crosses every construct that binds a name with every place code
outside the block can read one: before the block, after it, in a nested
function, a lambda, an ``except`` handler, a comprehension, a class body, and
a nested ``nonlocal``. The controls are the ways the name keeps its scope:
another binding outside the block, a ``global`` declaration, a parameter, and
nested scopes that bind the name for themselves. The engine tests show the
pair declined under ``moves_only_binding`` and kept where the call assigns
the name back; ``test_binding_collectors_match_cpython`` checks the function
against CPython's symbol table on generated code, and the hostile battery
checks every change Towel renders the same way (``ScopeWatch``).
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
import sys
import textwrap
from typing import Dict, List, Tuple

import pytest

from towel.unification.function_scope import identifiers, scope_moving_names
from towel.unification.models import FunctionNode
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer

BINDERS: Dict[str, str] = {
    "assign": "X = n",
    "tuple_target": "a, X = n, n",
    "starred_target": "a, *X = [n, n]",
    "augmented": "X += n",
    "annotated": "X: int = n",
    "bare_annotation": "X: int",
    "delete": "del X",
    "for": "for X in range(n):\n    pass",
    "with": "with open(n) as X:\n    pass",
    "except_as": "try:\n    pass\nexcept OSError as X:\n    pass",
    "import": "import X",
    "import_from": "from os import sep as X",
    "walrus": "print(X := n)",
    "walrus_in_comprehension": "ys = [(X := v) for v in range(n)]",
    "match_capture": "match n:\n    case X:\n        pass",
    "match_star": "match n:\n    case [*X]:\n        pass",
    "match_mapping_rest": "match n:\n    case {**X}:\n        pass",
    "def": "def X():\n    pass",
    "class": "class X:\n    pass",
}
"""Every construct that binds ``X`` in the function's own scope, as the block."""
if sys.version_info >= (3, 12):
    BINDERS["type_alias"] = "type X = int"

READS: Dict[str, Tuple[str, str]] = {
    "before": ("if n < 0:\n    return X", ""),
    "after": ("", "print(X)"),
    "nested_function": ("def g():\n    return X", ""),
    "lambda": ("g = lambda: X", ""),
    "except_handler": ("try:\n    h()\nexcept ValueError:\n    return X", ""),
    "comprehension": ("ys = [X for _ in range(n)]", ""),
    "class_body": ("class K:\n    v = X", ""),
    "method": ("class K:\n    def m(self):\n        return X", ""),
    "nonlocal": ("def g():\n    nonlocal X\n    X = 1", ""),
    "generic_bound": ("", "def g[T: X](): pass") if sys.version_info >= (3, 12) else ("", ""),
}
"""Code outside the block that reads ``X`` through the function: (before it, after it)."""


def _function(before: str, block: str, after: str) -> Tuple[FunctionNode, List[ast.stmt]]:
    """``def f(n)`` with ``block`` between ``before`` and ``after``, and the block's statements."""
    parts = [textwrap.indent(part, "    ") for part in (before, block, after) if part]
    tree = ast.parse("def f(n):\n" + "\n".join(parts) + "\n    return n\n")
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    start = len(ast.parse(before).body) if before else 0
    return function, function.body[start : start + len(ast.parse(block).body)]


@pytest.mark.parametrize("read", [name for name, parts in READS.items() if any(parts)])
@pytest.mark.parametrize("binder", list(BINDERS))
def test_r9bd_every_binder_read_anywhere_outside_the_block_moves_its_scope(
    binder: str, read: str
) -> None:
    before, after = READS[read]
    function, block = _function(before, BINDERS[binder], after)
    assert scope_moving_names(function, block) == {"X"}


KEPT: Dict[str, Tuple[str, str, str]] = {
    "bound_outside_too": ("X = 0\nif n < 0:\n    return X", "X = n", ""),
    "deleted_outside": ("if n < 0:\n    return X", "X = n", "del X"),
    "declared_global": ("global X\nif n < 0:\n    return X", "X = n", ""),
    "read_only_inside": ("print(n)", "X = n\nprint(X)", "print(n)"),
    "nested_parameter": ("def g(X):\n    return X", "X = n", ""),
    "nested_local": ("def g():\n    X = 1\n    return X", "X = n", ""),
    "nested_global": ("def g():\n    global X\n    return X", "X = n", ""),
    "lambda_parameter": ("g = lambda X: X", "X = n", ""),
    "comprehension_target": ("ys = [X for X in range(n)]", "for X in ys:\n    print(X)", ""),
    "class_attribute": ("class K:\n    X = 1\n    v = X", "X = n", ""),
    "nested_scope_in_the_block": ("", "X = n\ng = lambda: X", ""),
    "local_annotation_only": ("v: X = 1", "X = n", ""),
}
"""Each way the name keeps its scope, or nothing outside the block reads it: (before, block, after)."""


@pytest.mark.parametrize("case", list(KEPT))
def test_r9bd_a_name_that_keeps_its_scope_is_not_named(case: str) -> None:
    function, block = _function(*KEPT[case])
    assert scope_moving_names(function, block) == frozenset()


def test_r9bd_parameters_and_the_function_s_own_declarations_keep_their_scope() -> None:
    tree = ast.parse("def f(X):\n    if X:\n        return X\n    X = 1\n    print(X)\n")
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert scope_moving_names(function, function.body[1:]) == frozenset()


REPRODUCER = """\
total = "module total"


def summarize_a(items):
    if not items:
        return ("empty", total)
    total = sum(items)
    total = total * 2
    print("doubled", total)
    return ("ok", len(items))


def summarize_b(rows):
    if not rows:
        return ("empty", total)
    total = sum(rows)
    total = total * 2
    print("doubled", total)
    return ("ok", len(rows))
"""


def _rejections(path: Path, caplog: pytest.LogCaptureFixture) -> List[str]:
    with caplog.at_level(logging.DEBUG, logger="towel.rejections"):
        proposals = UnificationRefactorEngine(min_lines=3).analyze_file(str(path))
    assert not proposals
    return [record.getMessage() for record in caplog.records if record.name == "towel.rejections"]


def test_r9bd_the_audit_s_pair_is_declined_under_its_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "m.py"
    path.write_text(REPRODUCER)
    traced = _rejections(path, caplog)
    assert any(line.startswith("REJECT[moves_only_binding]") for line in traced), traced
    assert any(line.endswith("['total']") for line in traced), traced


def test_r9bd_a_call_that_assigns_the_name_back_keeps_it_local(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    # Each function now reads total after the block too, so the call assigns it.
    path.write_text(REPRODUCER.replace("len(items))", "total)").replace("len(rows))", "total)"))
    proposals = UnificationRefactorEngine(min_lines=3).analyze_file(str(path))
    calls = [
        ast.unparse(replacement.node)
        for proposal in proposals
        for replacement in proposal.replacements
    ]
    assert calls and all(call.startswith("total = ") for call in calls), calls


@pytest.mark.parametrize(
    "binding",
    ["id += 1", "del id", "a, *id = [1, 2]", "type id = int"][
        : 4 if sys.version_info >= (3, 12) else 3
    ],
)
def test_r9bd_a_builtin_spelling_any_construct_binds_stays_free(binding: str) -> None:
    """``id += 1`` alone makes ``id`` a local: a helper reading it bare would find the builtin."""
    source = f"def f():\n    try:\n        {binding}\n    except Exception:\n        pass\n"
    tree = ast.parse(source + "    return id, len\n")
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert analyzer.free_variables(function.body[-1:]) == {"id"}


def test_r9bd_identifiers_are_every_name_a_statement_spells() -> None:
    """What a declaration must agree on: binders that are no ``Name`` node count too."""
    source = textwrap.dedent("""
        import os.path as joined, sys
        from os import sep
        global declared
        try:
            pass
        except OSError as caught:
            pass
        match subject:
            case [captured, *starred] | {"k": captured, **starred}:
                pass
        def defined(parameter):
            nonlocal outer
        class Made:
            pass
        """)
    found = identifiers(ast.parse(source).body)
    assert found == {
        "joined",
        "sys",
        "sep",
        "declared",
        "caught",
        "subject",
        "captured",
        "starred",
        "defined",
        "parameter",
        "outer",
        "Made",
        "OSError",
    }
