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

"""Every identifier a class body's name mangling rewrites is seen as class-private, and no other.

CPython mangles a ``__name`` wherever the compiler stores, loads or imports
it by name: a name, an attribute, a parameter, the name a ``def`` or
``class`` binds, a ``global`` or ``nonlocal`` declaration, an ``except``
or ``match`` capture, a type parameter, an import's module and member and
the name it binds. It passes a call's keywords and a class pattern's
keywords on as written. So in ``class A`` the lambda parameter ``__p`` is
``_A__p`` while ``__p=`` is not, and ``import __tool`` imports ``_A__tool``.
Code moved out of the class, or into a helper whose error messages carry
another name, then behaves differently. The positions are checked against
the running interpreter, and each hazard through the CLI against what the
original and refactored programs print.
"""

from __future__ import annotations

import ast
import inspect
import io
import os
import subprocess
import symtable
import sys
import textwrap
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List, Set, Tuple
from unittest.mock import patch

import pytest

from towel import cli
from towel.unification.class_private import is_class_private, mangled, rewritten_identifiers
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import (
    _binds,
    created_object_escapes,
    uses_class_private_names,
)

POSITIONS: List[Tuple[str, str]] = [
    ("name read", "print(__q)"),
    ("name bound", "__q = 1"),
    ("attribute", "self.__q = 1"),
    ("parameter", "f = lambda __q=0: 0"),
    ("keyword-only parameter", "f = lambda *, __q=0: 0"),
    ("positional-only parameter", "f = lambda __q, /: 0"),
    ("star parameter", "f = lambda *__q: 0"),
    ("double-star parameter", "f = lambda **__q: 0"),
    ("def name", "def __q():\n    pass"),
    ("async def name", "async def __q():\n    pass"),
    ("class name", "class __q:\n    pass"),
    ("global", "global __q"),
    ("except name", "try:\n    pass\nexcept ValueError as __q:\n    pass"),
    ("match capture", "match x:\n    case __q:\n        pass"),
    ("match as", "match x:\n    case (1 as __q):\n        pass"),
    ("match star", "match x:\n    case [*__q]:\n        pass"),
    ("match rest", "match x:\n    case {**__q}:\n        pass"),
    ("import", "import __q"),
    ("import binding a dotted module's package", "import __q.sub"),
    ("import binding", "import os as __q"),
    ("import module", "import __q as m"),
    ("import dotted module", "import __q.sub as m"),
    ("from module", "from __q import m"),
    ("from dotted module", "from __q.sub import m"),
    ("from relative module", "from .__q import m"),
    ("from member", "from os import __q"),
    ("from member bound as", "from os import __q as m"),
    ("from binding", "from os import path as __q"),
    ("call keyword", "f(__q=1)"),
    ("class keyword", "class C(__q=1):\n    pass"),
    ("class pattern keyword", "match x:\n    case C(__q=1):\n        pass"),
    ("string", "'__q'"),
]
if sys.version_info >= (3, 12):
    POSITIONS += [
        ("type parameter", "def f[__q]():\n    pass"),
        ("parameter specification", "def f[**__q]():\n    pass"),
        ("type variable tuple", "def f[*__q]():\n    pass"),
        # Mangled with the generic class's own name, in its type-parameter scope.
        ("generic class parameter", "class C[__q]:\n    pass"),
        ("type alias parameter", "type T[__q] = int"),
    ]


def _in_class(snippet: str) -> str:
    return "class A:\n    def m(self, x, f, C):\n" + textwrap.indent(snippet, "        ") + "\n"


def _compiled_names(source: str) -> Set[str]:
    """Every name CPython records for ``source``: its symbol tables, and its code's names and strings."""
    names: Set[str] = set()
    tables = [symtable.symtable(source, "<case>", "exec")]
    while tables:
        table = tables.pop()
        names.update(table.get_identifiers())
        tables.extend(table.get_children())
    codes = [compile(source, "<case>", "exec")]
    while codes:
        code = codes.pop()
        names.update(code.co_names, code.co_varnames, code.co_cellvars, code.co_freevars)
        for constant in code.co_consts:
            if isinstance(constant, types.CodeType):
                codes.append(constant)
            elif isinstance(constant, str):
                names.add(constant)
            elif isinstance(constant, tuple):
                names.update(item for item in constant if isinstance(item, str))
    return names


def _towel_sees(source: str) -> bool:
    return any(
        name == "__q"
        for node in ast.walk(ast.parse(source))
        for name in rewritten_identifiers(node)
        if is_class_private(name)
    )


@pytest.mark.parametrize(
    "snippet", [snippet for _, snippet in POSITIONS], ids=[label for label, _ in POSITIONS]
)
def test_rewritten_positions_are_the_interpreters(snippet: str) -> None:
    source = _in_class(snippet)
    # The snippet spells ``__q`` in one position; CPython rewrote it there
    # when some ``_Class__q`` is among the names it recorded.
    rewritten = any(
        name.endswith("__q") and name.startswith("_") and name != "__q"
        for name in _compiled_names(source)
    )
    assert _towel_sees(source) is rewritten


def test_a_nonlocal_declaration_is_rewritten_as_the_interpreter_rewrites_it() -> None:
    # It compiles only if ``__q`` names the enclosing ``_A__q``.
    source = _in_class("_A__q = 0\ndef inner():\n    nonlocal __q\n    return 1")
    compile(source, "<case>", "exec")
    declaration = next(
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Nonlocal)
    )
    assert list(rewritten_identifiers(declaration)) == ["__q"]


@pytest.mark.parametrize(
    "block, private",
    [
        ("z = (lambda __p=0: 7)(__p=y)", True),
        ("import __tool as t", True),
        ("from __tool import NAME", True),
        ("from tool import __member as member", True),
        ("from __tool.sub import NAME", False),
        ("z = f(__p=y)", False),
        ("z = (lambda p=0: 7)(p=y)", False),
        ("z = self.__dict__", False),
    ],
)
def test_the_class_private_guard_sees_every_rewritten_position(block: str, private: bool) -> None:
    assert uses_class_private_names(ast.parse(block).body) is private


SIGNATURES = ["__p=0", "__p", "*, __p", "*, __p=0", "__p, /", "a, __p=0, **kw", "*args, **__p"]
CALLS: List[Tuple[int, Tuple[str, ...]]] = [
    (0, ()),
    (1, ()),
    (0, ("__p",)),
    (0, ("_A__p",)),
    (1, ("__p",)),
    (1, ("_A__p",)),
    (0, ("a", "__p")),
]


@pytest.mark.parametrize("signature", SIGNATURES)
def test_binding_in_a_class_is_decided_as_python_decides_it(signature: str) -> None:
    namespace: dict[str, object] = {}
    exec(
        f"class A:\n    function = lambda {signature}: None\n", namespace
    )  # noqa: S102 - a literal class
    function = vars(namespace["A"])["function"]
    assert callable(function)
    lambda_node = ast.parse(f"lambda {signature}: None", mode="eval").body
    assert isinstance(lambda_node, ast.Lambda)
    for positional, keywords in CALLS:
        try:
            inspect.signature(function).bind(*range(positional), **dict.fromkeys(keywords, 0))
            expected = True
        except TypeError:
            expected = False
        stored = _binds(lambda_node.args, positional, keywords, lambda name: mangled(name, "A"))
        assert stored is expected, (positional, keywords)


def _method(tree: ast.Module) -> ast.FunctionDef:
    """The first method of the module's first class."""
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    method = cls.body[0]
    assert isinstance(method, ast.FunctionDef)
    return method


@pytest.mark.parametrize(
    "call, escapes",
    [
        # ``__p=`` binds no parameter: the one written ``__p`` is ``_A__p``.
        ("(lambda __p=0: 7)(__p=y)", True),
        ("(lambda __p=0: 7)(_A__p=y)", False),
        ("(lambda p=0: 7)(p=y)", False),
    ],
)
def test_a_lambda_in_a_method_binds_keywords_to_its_parameters_as_stored(
    call: str, escapes: bool
) -> None:
    source = f"class A:\n    def m(self, y):\n        z = {call}\n        return z\n"
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    method = _method(tree)
    assert created_object_escapes(analyzer, method, method.body[:1]) is escapes


def test_a_private_parameter_of_a_function_the_tree_does_not_hold_binds_nothing() -> None:
    source = (
        "class A:\n    def m(self, y):\n        z = (lambda __p=0: 7)(_A__p=y)\n        return z\n"
    )
    analyzer = ScopeAnalyzer()
    analyzer.analyze(ast.parse(source))
    # Another parse of the same text: its method is not a node of the analyzed tree.
    method = _method(ast.parse(source))
    assert created_object_escapes(analyzer, method, method.body[:1])


# The audit's case: neither method reads ``self``, so a helper would be a
# module-level function, where ``__p`` is not mangled and the call returns 7.
AUDITED = """
class A:
    def m1(self, x):
        y = x + 1
        z = (lambda __p=0: 7)(__p=y)
        print("m1", y, z)

    def m2(self, x):
        y = x + 1
        z = (lambda __p=0: 7)(__p=y)
        print("m2", y, z)
"""

DRIVER = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("subject", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for method in (module.A().m1, module.A().m2):
    try:
        method(1)
    except TypeError as error:
        print("raised", error)
"""


def test_a_mangled_parameter_passed_by_keyword_keeps_its_behavior(tmp_path: Path) -> None:
    source = tmp_path / "subject.py"
    source.write_text(AUDITED)
    output = tmp_path / "out" / "subject.py"
    arguments = ["towel", "dry", str(source), str(output), "--no-interactive", "--no-types"]
    with (
        patch.object(sys, "argv", [*arguments, "--progress", "none"]),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        cli.main()

    def prints(path: Path) -> str:
        return subprocess.run(
            [sys.executable, "-c", DRIVER, str(path)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        ).stdout

    assert "raised" in prints(source)
    assert prints(output) == prints(source)
