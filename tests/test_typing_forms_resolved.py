"""A typing form is whatever its module binds to typing's object, however it is spelled.

A checker reads ``cast(Alpha, value)`` as typing's cast because ``cast`` is
bound to typing's function, not because of its name: ``from typing import
cast as c`` makes ``c(Alpha, value)`` one, ``import typing as t`` makes
``t.cast`` one, and so does a project module that re-exports it. Blocks that
differ where such a form is read are not duplicates, whether the form reads
a string (``TV("T")``) or a type written as an expression (``cast(Alpha,
value)``): the helper would say ``cast(__param_0, value)``, which checks under
no annotation of ``__param_0``. A project's own function that shares a form's
name is an ordinary call whose arguments may still become parameters:
sqlglot's ``exp.cast(column, to)`` has 166 calls in sqlglot itself.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import Dict, List, Set, Tuple

import pytest

from tests.test_helpers import refactor_to_fixed_point_silently, write_file
from towel.unification.refactor_engine import UnificationRefactorEngine

PYPROJECT = '[project]\nname = "sample"\nversion = "0"\n'

CLASSES = """
class Alpha:
    name = "alpha"


class Beta:
    name = "beta"
"""


def _pair(header: str, first: str, second: str, *, prelude: str = "") -> str:
    """Two functions sharing a block that differs only in ``first`` against ``second``."""

    def function(name: str, form: str) -> str:
        body = [
            *prelude.splitlines(),
            f"value = {form}",
            "label = value.name.upper()",
            'return label + "!"',
        ]
        return f"def {name}(raw):\n" + "".join(f"    {line}\n" for line in body)

    return f"{header}\n{CLASSES}\n\n{function('first', first)}\n\n{function('second', second)}"


def _calls(source: str) -> Set[str]:
    """Every call and subscript in ``source``, as ``ast.unparse`` spells it."""
    return {
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Call, ast.Subscript))
    }


def _refactor(tmp_path: Path, files: Dict[str, str], target: str) -> Tuple[str, int]:
    """Write a project of ``files`` and refactor ``target`` in it to a fixed point."""
    write_file(tmp_path / "pyproject.toml", PYPROJECT)
    for name, text in files.items():
        write_file(tmp_path / name, text)
    return refactor_to_fixed_point_silently(str(tmp_path / target), 1)


# Each case: the module's imports, and the form as each block spells it.
PINNED: Dict[str, Tuple[str, str, str]] = {
    "TypeVar under another name": (
        "from typing import TypeVar as TV",
        'TV("T") and Alpha',
        'TV("U") and Alpha',
    ),
    "cast of a type written as an expression": (
        "from typing import cast",
        "cast(Alpha, raw)",
        "cast(Beta, raw)",
    ),
    "cast through the typing module under another name": (
        "import typing as t",
        "t.cast(Alpha, raw)",
        "t.cast(Beta, raw)",
    ),
    "cast under another name, of a generic type": (
        "from typing import cast as c",
        "c(list[Alpha], raw)[0]",
        "c(list[Beta], raw)[0]",
    ),
    "cast from typing_extensions": (
        "import typing_extensions as te",
        "te.cast(Alpha, raw)",
        "te.cast(Beta, raw)",
    ),
    "assert_type of a type written as an expression": (
        "from typing_extensions import assert_type as check",
        "check(raw, Alpha)",
        "check(raw, Beta)",
    ),
    "Literal under another name": (
        "from typing import Literal as L, get_args",
        'get_args(L["alpha"]) and Alpha',
        'get_args(L["beta"]) and Alpha',
    ),
    "NewType under another name": (
        "from typing_extensions import NewType as NT",
        'NT("UserId", int) and Alpha',
        'NT("OrderId", int) and Alpha',
    ),
    "namedtuple under another name": (
        "from collections import namedtuple as record",
        'record("Point", "x y") and Alpha',
        'record("Point", "x z") and Alpha',
    ),
    "Enum under another name": (
        "from enum import Enum as Choice",
        'Choice("Color", "RED GREEN") and Alpha',
        'Choice("Color", "RED BLUE") and Alpha',
    ),
    "cast bound on one path of two": (
        "try:\n    from typing import cast\nexcept ImportError:\n    def cast(kind, value):\n        return value",
        "cast(Alpha, raw)",
        "cast(Beta, raw)",
    ),
    "cast from a star import of typing": (
        "from typing import *",
        "cast(Alpha, raw)",
        "cast(Beta, raw)",
    ),
}


@pytest.mark.parametrize("case", sorted(PINNED))
def test_a_form_stays_where_its_module_binds_it_to_typing(tmp_path: Path, case: str) -> None:
    header, first, second = PINNED[case]
    source = _pair(header, first, second)
    final, _applied = _refactor(tmp_path, {"sample.py": source}, "sample.py")
    kept = _calls(final)
    for form in (first, second):
        written = _calls(form)
        assert written <= kept, (sorted(written - kept), final)


def test_a_form_a_function_imports_for_itself_stays_too(tmp_path: Path) -> None:
    source = _pair("", "cast(Alpha, raw)", "cast(Beta, raw)", prelude="from typing import cast\n")
    final, _applied = _refactor(tmp_path, {"sample.py": source}, "sample.py")
    assert {"cast(Alpha, raw)", "cast(Beta, raw)"} <= _calls(final), final


def test_a_form_a_module_of_the_project_re_exports_stays_too(tmp_path: Path) -> None:
    final, _applied = _refactor(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/compat.py": "from typing import cast as cast\n",
            "pkg/sample.py": _pair(
                "from .compat import cast", "cast(Alpha, raw)", "cast(Beta, raw)"
            ),
        },
        "pkg/sample.py",
    )
    assert {"cast(Alpha, raw)", "cast(Beta, raw)"} <= _calls(final), final


def test_blocks_of_one_structure_calling_different_casts_are_judged_apart(
    tmp_path: Path,
) -> None:
    # One engine memoizes unification by the blocks' structure, which these
    # two modules share; what ``cast`` is bound to is part of the key.
    write_file(tmp_path / "pyproject.toml", PYPROJECT)
    own = _pair("def cast(kind, value):\n    return value\n", "cast(Alpha, raw)", "cast(Beta, raw)")
    typed = _pair("from typing import cast", "cast(Alpha, raw)", "cast(Beta, raw)")
    engine = UnificationRefactorEngine(min_lines=1)
    finals: Dict[str, str] = {}
    for name, source in (("own.py", own), ("typed.py", typed)):
        write_file(tmp_path / name, source)
        with contextlib.redirect_stdout(io.StringIO()):
            finals[name], _applied, _ = engine.refactor_to_fixed_point(
                str(tmp_path / name), max_iterations=0, progress="none"
            )
    assert _parameterized_calls(finals["own.py"], "cast"), finals["own.py"]
    assert {"cast(Alpha, raw)", "cast(Beta, raw)"} <= _calls(finals["typed.py"]), finals["typed.py"]


def _helpers(source: str) -> List[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        and re.fullmatch(r"_{1,2}extracted_func_\d+", node.name)
    ]


def _parameterized_calls(source: str, callee: str) -> List[str]:
    """The calls of ``callee`` in generated helpers that pass one of the helper's parameters."""
    return [
        ast.unparse(node)
        for helper in _helpers(source)
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == callee
        and "__param_" in ast.unparse(node)
    ]


SQLGLOT_PAIR = """
from sqlglot import exp


def first(raw):
    node = exp.cast(exp.column("a"), "INT")
    wrapped = node.as_(raw)
    return wrapped.sql()


def second(raw):
    node = exp.cast(exp.column("b"), "INT")
    wrapped = node.as_(raw)
    return wrapped.sql()
"""


def test_sqlglots_own_cast_is_an_ordinary_call_in_sqlglot(tmp_path: Path) -> None:
    # sqlglot's modules reach its expression builder as ``exp``, which
    # sqlglot/__init__.py binds to sqlglot.expressions, where ``cast`` is a
    # function of sqlglot's own taking the expression first.
    final, applied = _refactor(
        tmp_path,
        {
            "sqlglot/__init__.py": "from sqlglot import expressions as exp\n",
            "sqlglot/expressions.py": (
                "import typing as t\n\n\n"
                "def column(name):\n    return name\n\n\n"
                "def cast(expression, to):\n    return t.cast(str, expression)\n"
            ),
            "sqlglot/dialect.py": SQLGLOT_PAIR,
        },
        "sqlglot/dialect.py",
    )
    assert applied >= 1
    assert _parameterized_calls(final, "exp.cast"), final


def test_a_cast_imported_from_outside_the_project_is_taken_at_its_word(tmp_path: Path) -> None:
    # Neither sqlglot nor SQLAlchemy is part of this project: their ``cast``
    # is theirs, as the import says.
    final, applied = _refactor(tmp_path, {"sample.py": SQLGLOT_PAIR}, "sample.py")
    assert applied >= 1
    assert _parameterized_calls(final, "exp.cast"), final
    alchemy = """
    from sqlalchemy import Integer, cast


    def first(table):
        column = cast(table.first, Integer)
        labelled = column.label("value")
        return labelled.desc()


    def second(table):
        column = cast(table.second, Integer)
        labelled = column.label("value")
        return labelled.desc()
    """
    final, applied = _refactor(tmp_path, {"alchemy.py": textwrap.dedent(alchemy)}, "alchemy.py")
    assert applied >= 1
    assert _parameterized_calls(final, "cast"), final


def test_a_projects_own_cast_imported_from_its_own_module_is_an_ordinary_call(
    tmp_path: Path,
) -> None:
    final, applied = _refactor(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/compat.py": "def cast(kind, value):\n    return value\n",
            "pkg/sample.py": _pair(
                "from .compat import cast", 'cast("alpha", raw)', 'cast("beta", raw)'
            ),
        },
        "pkg/sample.py",
    )
    assert applied >= 1
    assert _parameterized_calls(final, "cast"), final


# Each form as written, spelled through an alias, a qualified name or
# typing_extensions, and with what a checker reads made a parameter,
# annotated as favourably as the parameter can be.
RESOLVED_FORMS: Dict[str, Tuple[str, str, str, str]] = {
    "TypeVar under another name": (
        "object",
        'T = TV("T")\nreturn T',
        "T = TV(p0)\nreturn T",
        'L["T"]',
    ),
    "cast through typing under another name": (
        "str",
        "value = t.cast(Alpha, x)\nreturn value.name",
        "value = t.cast(p0, x)\nreturn value.name",
        "type[Alpha]",
    ),
    "cast under another name, of a generic type": (
        "str",
        "value = c(list[Alpha], x)\nreturn value[0].name",
        "value = c(list[p0], x)\nreturn value[0].name",
        "type[Alpha]",
    ),
    "assert_type from typing_extensions": (
        "None",
        "te.assert_type(str(x), str)",
        "te.assert_type(str(x), p0)",
        "type[str]",
    ),
    "Literal under another name": (
        "str",
        'value = c(L["a"], x)\nreturn value',
        "value = c(L[p0], x)\nreturn value",
        'L["a"]',
    ),
    "NewType under another name": (
        "int",
        'U = NT("U", int)\nreturn U(5)',
        "U = NT(p0, int)\nreturn U(5)",
        'L["U"]',
    ),
    "Enum under another name": (
        "object",
        'Color = E("Color", "RED GREEN")\nreturn Color.RED',
        'Color = E("Color", p0)\nreturn Color.RED',
        'L["RED GREEN"]',
    ),
    "ParamSpec from typing_extensions": (
        "object",
        'P = te.ParamSpec("P")\nreturn P',
        "P = te.ParamSpec(p0)\nreturn P",
        'L["P"]',
    ),
    "NamedTuple through typing under another name": (
        "int",
        'Point = t.NamedTuple("Point", [("x", int)])\nreturn Point(1).x',
        'Point = t.NamedTuple(p0, [("x", int)])\nreturn Point(1).x',
        'L["Point"]',
    ),
    "TypedDict from typing_extensions": (
        "object",
        'Movie = te.TypedDict("Movie", {"name": str})\nreturn Movie(name="x")',
        'Movie = te.TypedDict("Movie", {p0: str})\nreturn Movie(name="x")',
        'L["name"]',
    ),
}

RESOLVED_HEADER = (
    "from enum import Enum as E\n"
    "from typing import Literal as L, NewType as NT, TypeVar as TV, cast as c\n"
    "import typing as t\n"
    "import typing_extensions as te\n"
    "\n"
    "\n"
    "class Alpha:\n"
    '    name = "alpha"\n'
    "\n"
    "\n"
    "def cast(expression: object, to: str) -> str:\n"
    "    return f'CAST({expression} AS {to})'\n"
)


def _resolved_module(parameterized: bool) -> Tuple[str, Dict[str, range]]:
    """A module with one function per form, and the lines each function spans."""
    lines = RESOLVED_HEADER.splitlines()
    spans: Dict[str, range] = {}
    for index, (name, (returns, original, changed, annotation)) in enumerate(
        RESOLVED_FORMS.items()
    ):
        signature = f"p0: {annotation}, x: object" if parameterized else "x: object"
        start = len(lines) + 3
        lines += [
            "",
            "",
            f"def case_{index}({signature}) -> {returns}:",
            *("    " + line for line in (changed if parameterized else original).split("\n")),
        ]
        spans[name] = range(start, len(lines) + 1)
    # A project's own function named like a form takes a parameter as any
    # call does: pinning it would decline an extraction for nothing.
    lines += [
        "",
        "",
        f"def own_cast({'p0: object, ' if parameterized else ''}x: object) -> str:",
        f"    return cast({'p0' if parameterized else 'x'}, 'INT')",
    ]
    return "\n".join(lines) + "\n", spans


def _strict_error_lines(tool: str, path: Path) -> Set[int]:
    """The lines ``tool`` reports errors on in strict mode, once it has provably run."""
    if tool == "mypy":
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--strict",
                "--no-incremental",
                "--python-version",
                "3.12",
                str(path),
            ],
            capture_output=True,
            text=True,
            cwd=path.parent,
            timeout=300,
        )
        assert "error:" in result.stdout or "Success" in result.stdout, (
            result.stdout + result.stderr
        )
        return {
            int(match.group(1))
            for match in re.finditer(r"^[^:]+:(\d+): error:", result.stdout, re.MULTILINE)
        }
    (path.parent / "pyrightconfig.json").write_text(
        json.dumps({"typeCheckingMode": "strict", "pythonVersion": "3.12"})
    )
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson", str(path)],
        capture_output=True,
        text=True,
        cwd=path.parent,
        timeout=300,
    )
    report = json.loads(result.stdout)
    assert report["summary"]["filesAnalyzed"] == 1, result.stdout
    return {
        diagnostic["range"]["start"]["line"] + 1
        for diagnostic in report["generalDiagnostics"]
        if diagnostic["severity"] == "error"
    }


@pytest.mark.skipif(
    importlib.util.find_spec("mypy") is None or importlib.util.find_spec("pyright") is None,
    reason="needs mypy and pyright",
)
def test_a_strict_checker_rejects_every_resolved_form_with_a_parameter_where_it_reads(
    tmp_path: Path,
) -> None:
    """Why these spellings are pinned: each checks as written, and none with a parameter.

    Under ``mypy --strict`` and pyright's strict mode both accept every form
    as written. With the literal or type a checker reads made a parameter,
    at least one rejects each (mypy the functional ``NamedTuple``, pyright
    ``Enum``'s members, both the rest), while a project's own function named
    ``cast`` takes the parameter as any call does. A checker release that
    changes one fails this test and returns the rule for review.
    """
    original, _ = _resolved_module(parameterized=False)
    (tmp_path / "original").mkdir()
    (tmp_path / "original" / "forms.py").write_text(original)
    for tool in ("mypy", "pyright"):
        assert _strict_error_lines(tool, tmp_path / "original" / "forms.py") == set(), tool
    changed, spans = _resolved_module(parameterized=True)
    (tmp_path / "changed").mkdir()
    (tmp_path / "changed" / "forms.py").write_text(changed)
    rejected = _strict_error_lines("mypy", tmp_path / "changed" / "forms.py") | (
        _strict_error_lines("pyright", tmp_path / "changed" / "forms.py")
    )
    accepted = [name for name, lines in spans.items() if not rejected & set(lines)]
    assert accepted == []
    own_cast = changed.splitlines().index("def own_cast(p0: object, x: object) -> str:") + 1
    assert not rejected & {own_cast, own_cast + 1}
