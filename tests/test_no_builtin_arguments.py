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

"""No helper takes a builtin as a parameter, in a same-module pair or across modules.

``helper(rows, name, len)`` would surprise every reader, so no generated call
may hand its helper a builtin: not as an argument, not as what a thunk
argument returns, and not inside a literal tuple, list, set or dict. A name a
site's function binds is that site's own local, so where both functions bind
``len``, each passes its own. Four paths used to hand one over:

- one site's function binds the builtin's name and the other's reads the
  builtin, so the name became a parameter that the second site filled with
  the builtin;
- a clustered block that reads the builtin joined a helper whose two sites
  pass their own local of that name;
- ``__import__`` and ``__debug__`` are builtins Towel's own list of builtins
  leaves out, so a block differing in one passed it as ``lambda: __import__``;
- a literal container of builtins differing from a plain name was passed as
  ``(int, str)`` or ``lambda: [int, str]``.

A thunk that calls a builtin, ``lambda: len(rows)``, hands over what the
builtin computed, and stays allowed.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, List, Mapping, Set, Tuple

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import builtins_passed

REAL_BUILTINS = frozenset(vars(builtins)) - {"__name__", "__doc__", "__package__", "__spec__"}


def _write(root: Path, files: Mapping[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"))


def _python_files(root: Path) -> Dict[str, str]:
    return {str(path.relative_to(root)): path.read_text() for path in sorted(root.rglob("*.py"))}


def _refactor(target: Path) -> Tuple[int, Mapping[str, int]]:
    """Refactor ``target`` in place, a module or a package; applied count and declines by reason.

    A package's modules share helpers only on request (``cross_module_helpers``),
    which its cases make; a module is refactored in the default mode.
    """
    engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=target.is_dir())
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if target.is_dir():
            results, _ = engine.refactor_directory_to_fixed_point(
                str(target), str(target), progress="none"
            )
            applied = sum(count for count, _ in results.values())
        else:
            final, applied, _ = engine.refactor_to_fixed_point(str(target), progress="none")
            target.write_text(final)
    return applied, engine.declined_pairs


def _run(root: Path, script: str) -> str:
    completed = subprocess.run(
        [sys.executable, script],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return completed.stdout


def _function_locals(function: ast.FunctionDef) -> Set[str]:
    """The parameters and the names ``function``'s own body assigns."""
    arguments = function.args
    names = {
        argument.arg
        for argument in [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *filter(None, [arguments.vararg, arguments.kwarg]),
        ]
    }
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _values(node: ast.expr, shadowed: Set[str]) -> Set[str]:
    """The names that are ``node``'s value, a lambda's result, or its literal container's members."""
    if isinstance(node, ast.Lambda):
        own = {argument.arg for argument in node.args.args}
        return _values(node.body, shadowed | own)
    if isinstance(node, ast.Name):
        return set() if node.id in shadowed else {node.id}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set().union(*(_values(element, shadowed) for element in node.elts))
    if isinstance(node, ast.Dict):
        members = [*(key for key in node.keys if key is not None), *node.values]
        return set().union(*(_values(member, shadowed) for member in members))
    return set()


def _handed_builtins(sources: Mapping[str, str]) -> List[str]:
    """Every builtin a helper call in ``sources`` hands over, as ``module:function:name``."""
    found = []
    for module, source in sources.items():
        for function in ast.walk(ast.parse(source)):
            if not isinstance(function, ast.FunctionDef):
                continue
            local = _function_locals(function)
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and "extracted_func" in node.func.id
                ):
                    for argument in [*node.args, *(k.value for k in node.keywords)]:
                        for name in sorted((_values(argument, set()) & REAL_BUILTINS) - local):
                            found.append(f"{module}:{function.name}:{name}")
    return found


def _helper_calls(source: str) -> Dict[str, List[str]]:
    """Each function's calls of a generated helper, unparsed."""
    return {
        function.name: [
            ast.unparse(node)
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and "extracted_func" in node.func.id
        ]
        for function in ast.parse(source).body
        if isinstance(function, ast.FunctionDef)
    }


BLOCK = """
    width = len(name)
    height = len(rows)
    area = width * height
    return area + 1
"""


def _function(header: str, tag: str) -> str:
    return f"def {header}:\n    print({tag!r})\n" + textwrap.indent(
        textwrap.dedent(BLOCK).strip("\n"), "    "
    )


PLAIN = "first(rows, name)"
LOCAL_30 = "first(rows, name, len=lambda value: 30)"
LOCAL_40 = "second(rows, name, len=lambda value: 40)"

SAME_MODULE_DRIVER = """
from unittest import mock
import m

def show():
    print([f([1, 2], "abc") for f in (m.first, m.second, getattr(m, "third", m.first))])

show()
with mock.patch("m.len", lambda value: 100):
    show()
"""


def test_a_site_reading_the_builtin_does_not_share_with_one_binding_its_name(
    tmp_path: Path,
) -> None:
    source = _function(PLAIN, "first") + "\n\n\n" + _function(LOCAL_40, "second") + "\n"
    _write(tmp_path, {"m.py": source, "drive.py": SAME_MODULE_DRIVER})
    applied, declined = _refactor(tmp_path / "m.py")
    assert applied == 0
    assert (tmp_path / "m.py").read_text() == source
    assert "builtin_argument" in declined


def test_two_sites_binding_the_name_each_pass_their_own(tmp_path: Path) -> None:
    source = _function(LOCAL_30, "first") + "\n\n\n" + _function(LOCAL_40, "second") + "\n"
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": source, "drive.py": SAME_MODULE_DRIVER})
    applied, _ = _refactor(after / "m.py")
    assert applied == 1
    calls = _helper_calls((after / "m.py").read_text())
    assert [len(calls["first"]), len(calls["second"])] == [1, 1]
    assert all(", len, " in call for call in calls["first"] + calls["second"])
    assert _handed_builtins(_python_files(after)) == []
    assert _run(after, "drive.py") == _run(before, "drive.py")


def test_a_clustered_site_reading_the_builtin_keeps_its_code(tmp_path: Path) -> None:
    source = "\n\n\n".join(
        [
            _function(LOCAL_30, "first"),
            _function(LOCAL_40, "second"),
            _function("third(rows, name)", "third"),
        ]
    )
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": source + "\n", "drive.py": SAME_MODULE_DRIVER})
    applied, _ = _refactor(after / "m.py")
    assert applied == 1
    calls = _helper_calls((after / "m.py").read_text())
    assert calls["third"] == []
    assert len(calls["first"]) == len(calls["second"]) == 1
    assert _handed_builtins(_python_files(after)) == []
    assert _run(after, "drive.py") == _run(before, "drive.py")


# Blocks that differ in a builtin value only: a builtin Towel's list leaves
# out, and literal containers of builtins against a plain name.
DIFFERING_IN_A_BUILTIN = {
    "dunder_import": (
        """
        def load(name):
            return name.upper()


        def first(name, rows):
            print("first")
            loader = __import__
            module = loader(name)
            rows.append(module)
            return len(rows)


        def second(name, rows):
            print("second")
            loader = load
            module = loader(name)
            rows.append(module)
            return len(rows)
        """,
        'print(m.first("json", []), m.second("json", []))',
    ),
    "dunder_debug": (
        """
        def first(rows, flag):
            print("first")
            checking = __debug__
            total = len(rows) if checking else 0
            total = total * 2
            return total


        def second(rows, flag):
            print("second")
            checking = flag
            total = len(rows) if checking else 0
            total = total * 2
            return total
        """,
        "print(m.first([1, 2], False), m.second([1, 2], False))",
    ),
    "tuple_of_builtins": (
        """
        def record(**fields):
            return fields


        def first(value, rows, name):
            print("first")
            entry = record(value=value, name=name, kinds=(int, str), size=len(rows), head=rows[0])
            rows.append(entry)
            return len(rows) + 1


        def second(value, rows, name, options):
            print("second")
            entry = record(value=value, name=name, kinds=options, size=len(rows), head=rows[0])
            rows.append(entry)
            return len(rows) + 1
        """,
        'print(m.first(1, [0], "a"), m.second(1, [0], "a", (float,)))',
    ),
    "list_of_builtins": (
        """
        def record(**fields):
            return fields


        def first(value, rows, name):
            print("first")
            entry = record(value=value, name=name, kinds=[int, str], size=len(rows), head=rows[0])
            rows.append(entry)
            return len(rows) + 1


        def second(value, rows, name, options):
            print("second")
            entry = record(value=value, name=name, kinds=options, size=len(rows), head=rows[0])
            rows.append(entry)
            return len(rows) + 1
        """,
        'print(m.first(1, [0], "a"), m.second(1, [0], "a", [float]))',
    ),
}


@pytest.mark.parametrize("case", sorted(DIFFERING_IN_A_BUILTIN))
def test_blocks_differing_in_a_builtin_value_hand_over_no_builtin(
    tmp_path: Path, case: str
) -> None:
    source, driver = DIFFERING_IN_A_BUILTIN[case]
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": source, "drive.py": "import m\n" + driver + "\n"})
    _refactor(after / "m.py")
    assert _handed_builtins(_python_files(after)) == []
    assert _run(after, "drive.py") == _run(before, "drive.py")


def _package(exports_header: str, reports_header: str) -> Dict[str, str]:
    """``exports`` already imports ``reports``, so the helper, if any, lives in ``reports``."""
    return {
        "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
        "pkg/__init__.py": "",
        "pkg/exports.py": "from pkg import reports  # exports already depends on reports\n\n\n"
        + _function(exports_header, "exports")
        + "\n",
        "pkg/reports.py": _function(reports_header, "reports") + "\n",
    }


def test_across_modules_a_site_binding_the_name_does_not_share_with_one_reading_it(
    tmp_path: Path,
) -> None:
    files = _package("export_size(rows, name, len=lambda value: 40)", "report_size(rows, name)")
    _write(tmp_path, files)
    before = _python_files(tmp_path)
    applied, declined = _refactor(tmp_path / "pkg")
    assert applied == 0
    assert _python_files(tmp_path) == before
    assert "builtin_argument" in declined


def test_across_modules_two_sites_binding_the_name_each_pass_their_own(tmp_path: Path) -> None:
    files = _package(
        "export_size(rows, name, len=lambda value: 40)",
        "report_size(rows, name, len=lambda value: 30)",
    )
    _write(tmp_path, files)
    applied, _ = _refactor(tmp_path / "pkg")
    assert applied > 0
    assert _handed_builtins(_python_files(tmp_path)) == []


@pytest.mark.parametrize("case", ["dunder_import", "dunder_debug"])
def test_across_modules_blocks_differing_in_a_builtin_hand_over_none(
    tmp_path: Path, case: str
) -> None:
    source, driver = DIFFERING_IN_A_BUILTIN[case]
    tree = ast.parse(textwrap.dedent(source))
    helpers = [node for node in tree.body if not isinstance(node, ast.FunctionDef)]
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    shared = [ast.unparse(node) for node in helpers] + [
        ast.unparse(function)
        for name, function in functions.items()
        if name not in ("first", "second")
    ]
    files = {
        "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
        "pkg/__init__.py": "",
        "pkg/exports.py": "\n\n\n".join(
            ["from pkg import reports  # exports already depends on reports", *shared]
            + [ast.unparse(functions["second"])]
        )
        + "\n",
        "pkg/reports.py": ast.unparse(functions["first"]) + "\n",
        "drive.py": "from pkg.reports import first\nfrom pkg.exports import second\n"
        + driver.replace("m.", "")
        + "\n",
    }
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    _refactor(after / "pkg")
    assert _handed_builtins(_python_files(after)) == []
    assert _run(after, "drive.py") == _run(before, "drive.py")


# What ``builtins_passed`` counts, in one call of the helper.
HANDED = {
    "len": {"len"},
    "(int, str)": {"int", "str"},
    "[int, [str]]": {"int", "str"},
    "{int}": {"int"},
    "{'k': float}": {"float"},
    "lambda: __import__": {"__import__"},
    "lambda: (int, str)": {"int", "str"},
    "lambda: len(rows)": set(),
    "lambda item: item": set(),
    "lambda str: str": set(),
    "rows": set(),
    "own": set(),
    "str.upper": set(),
}


@pytest.mark.parametrize("argument", sorted(HANDED))
def test_what_counts_as_handing_a_builtin_to_the_helper(argument: str) -> None:
    source = f"def site(rows, own=None):\n    return __extracted_func(rows, {argument})\n"
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    handed = builtins_passed(function.body[0], "__extracted_func", function, analyzer)
    assert handed == HANDED[argument]


def test_a_name_the_site_binds_is_its_own_local(tmp_path: Path) -> None:
    source = "def site(rows, len, str=None):\n    return __extracted_func(len, (str, rows))\n"
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert builtins_passed(function.body[0], "__extracted_func", function, analyzer) == set()
