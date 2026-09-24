"""``parameterize_builtins`` passes a builtin that may differ between sites instead of declining.

By default no helper takes a builtin as a parameter, and a pair is declined
wherever a builtin its moved code reads may differ between its sites: one
site's function binds ``len`` and the other reads the builtin, or, across
modules, the program shows a participating module may hold the name. With
``parameterize_builtins`` (``towel dry --parameterize-builtins``) exactly
those builtins become ordinary parameters, each site passing its own binding,
evaluated where the block read it, so a patch of either module still reaches
that module's code. It permits nothing else: a builtin every site reads alike
is still read bare, and blocks that differ in which builtin they use are still
declined.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Dict, List, Mapping, Optional, Tuple, TypedDict

import pytest

from towel.cli import DryOptions, PreviewOptions, _build_parser
from towel.unification.annotations import (
    _joined_revealed,
    annotation_from_revealed,
    builtin_object_revealed,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

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


def _write(root: Path, files: Mapping[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"))


def _python_files(root: Path) -> Dict[str, str]:
    return {str(path.relative_to(root)): path.read_text() for path in sorted(root.rglob("*.py"))}


def _refactor(target: Path, parameterize_builtins: bool) -> Tuple[int, Mapping[str, int]]:
    """Refactor ``target`` in place, a module or a package; applied count and declines by reason.

    A package's modules share helpers only on request (``cross_module_helpers``),
    which its cases make; a module is refactored in the default mode.
    """
    engine = UnificationRefactorEngine(
        min_lines=3,
        parameterize_builtins=parameterize_builtins,
        cross_module_helpers=target.is_dir(),
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if target.is_dir():
            results, _ = engine.refactor_directory_to_fixed_point(
                str(target), str(target), progress="none"
            )
            return sum(count for count, _ in results.values()), engine.declined_pairs
        final, applied, _ = engine.refactor_to_fixed_point(str(target), progress="none")
        target.write_text(final)
        return applied, engine.declined_pairs


def _run(cwd: Path, script: str, pythonpath: Optional[Path] = None) -> str:
    completed = subprocess.run(
        [sys.executable, script],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env={
            "PATH": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(pythonpath) if pythonpath else "",
        },
    )
    return completed.stdout


def _helpers(root: Path) -> List[ast.FunctionDef]:
    return [
        node
        for source in _python_files(root).values()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("__extracted_func")
    ]


def _parameters(root: Path) -> List[List[str]]:
    return [[argument.arg for argument in helper.args.args] for helper in _helpers(root)]


def _calls(source: str) -> Dict[str, int]:
    """How many calls of a generated helper each function of ``source`` makes."""
    return {
        function.name: sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("__extracted_func")
            for node in ast.walk(function)
        )
        for function in ast.parse(source).body
        if isinstance(function, ast.FunctionDef)
    }


# The oracle: ``len`` patched in each module in turn, with and without
# ``create=True`` (``mock`` creates a builtin's name anyway), and rebound.
PATCHING_DRIVER = """
from unittest import mock
import importlib

MISSING = object()
MODULES = {modules!r}
CALLS = {calls!r}


def show(label):
    results = []
    for module, function in CALLS:
        try:
            value = getattr(importlib.import_module(module), function)([1, 2], "abc")
        except Exception as error:
            value = "raised " + type(error).__name__
        results.append(repr(value))
    print(label, results, flush=True)


show("unpatched")
for module in MODULES:
    for create in (True, False):
        with mock.patch(module + ".len", lambda value: 100, create=create):
            show(module + " patched, create=" + str(create))
    target = importlib.import_module(module)
    saved = vars(target).get("len", MISSING)
    target.len = lambda value: 1000
    show(module + " rebound")
    if saved is MISSING:
        del target.len
    else:
        target.len = saved
with mock.patch("builtins.len", lambda value: 7):
    show("builtins patched")
"""


def _driver(modules: List[str], calls: List[Tuple[str, str]]) -> str:
    return PATCHING_DRIVER.format(modules=modules, calls=calls)


SAME_MODULE_DRIVER = _driver(["m"], [("m", "first"), ("m", "second"), ("m", "third")])

MIXED = "\n\n\n".join(
    [
        _function("first(rows, name)", "first"),
        _function("second(rows, name, len=lambda value: 40)", "second"),
        "def third(rows, name):\n    return first(rows, name)",
    ]
)

CLUSTERED = "\n\n\n".join(
    [
        _function("first(rows, name, len=lambda value: 30)", "first"),
        _function("second(rows, name, len=lambda value: 40)", "second"),
        _function("third(rows, name)", "third"),
    ]
)


@pytest.mark.parametrize("flag", [False, True])
def test_a_site_reading_the_builtin_shares_with_one_binding_its_name_only_with_the_flag(
    tmp_path: Path, flag: bool
) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": MIXED + "\n", "drive.py": SAME_MODULE_DRIVER})
    applied, declined = _refactor(after / "m.py", parameterize_builtins=flag)
    if flag:
        assert applied == 1
        assert _parameters(after) == [["__param_0", "len", "name", "rows"]]
    else:
        assert applied == 0
        assert "builtin_argument" in declined
    assert _run(after, "drive.py") == _run(before, "drive.py")


@pytest.mark.parametrize("flag", [False, True])
def test_a_clustered_site_reading_the_builtin_joins_only_with_the_flag(
    tmp_path: Path, flag: bool
) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        _write(root, {"m.py": CLUSTERED + "\n", "drive.py": SAME_MODULE_DRIVER})
    applied, _ = _refactor(after / "m.py", parameterize_builtins=flag)
    assert applied == 1
    calls = _calls((after / "m.py").read_text())
    assert (calls["first"], calls["second"], calls["third"]) == (1, 1, 1 if flag else 0)
    assert _run(after, "drive.py") == _run(before, "drive.py")


def _package(
    exports_prelude: str = "",
    exports_header: str = "export_size(rows, name)",
    reports_prelude: str = "",
    extra: Mapping[str, str] = {},
) -> Dict[str, str]:
    """``exports`` already imports ``reports``, so the helper, if any, lives in ``reports``."""
    exports = "\n\n\n".join(
        part
        for part in (
            "from pkg import reports  # exports already depends on reports",
            textwrap.dedent(exports_prelude).strip(),
            _function(exports_header, "exports"),
        )
        if part
    )
    reports = "\n\n\n".join(
        part
        for part in (
            textwrap.dedent(reports_prelude).strip(),
            _function("report_size(rows, name)", "reports"),
        )
        if part
    )
    return {
        "proj/pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
        "proj/pkg/__init__.py": "",
        "proj/pkg/exports.py": exports + "\n",
        "proj/pkg/reports.py": reports + "\n",
        # Outside the project: its patches are the oracle, not evidence.
        "outside/drive.py": _driver(
            ["pkg.exports", "pkg.reports"],
            [("pkg.exports", "export_size"), ("pkg.reports", "report_size")],
        ),
        **dict(extra),
    }


class _Evidence(TypedDict, total=False):
    """What a case adds to the package: preludes, a header for the borrower, more files."""

    exports_prelude: str
    exports_header: str
    reports_prelude: str
    extra: Mapping[str, str]


# Each form of evidence that a builtin may differ between the modules, which
# declines the pair by default and makes the builtin a parameter with the flag.
EVIDENCE: Dict[str, _Evidence] = {
    "the_borrower_binds_it": {"exports_prelude": "def len(item):\n    return 7"},
    "the_host_binds_it": {"reports_prelude": "def len(item):\n    return 7"},
    "star_import_in_the_project": {
        "exports_prelude": "from pkg.util import *",
        "extra": {"proj/pkg/util.py": "def len(item):\n    return 55\n"},
    },
    "star_import_outside_the_project": {"exports_prelude": "from os.path import *"},
    "rebound_builtins_namespace": {
        "exports_prelude": "import builtins\n__builtins__ = dict(vars(builtins), len=lambda i: 7)"
    },
    "written_into_the_namespace": {"exports_prelude": 'vars()["len"] = lambda item: 7'},
    "patched_by_the_tests": {"extra": {"proj/tests/test_exports.py": """
                from unittest import mock
                import pkg.exports

                def test_size():
                    with mock.patch("pkg.exports.len", lambda value: 100, create=True):
                        assert pkg.exports.export_size([1, 2], "ab") == 10001
                """}},
    "monkeypatched_by_the_tests": {"extra": {"proj/tests/test_exports.py": """
                from pkg import exports

                def test_size(monkeypatch):
                    monkeypatch.setattr(exports, "len", lambda value: 100, raising=False)
                """}},
    "one_site_binds_it_locally": {
        "exports_header": "export_size(rows, name, len=lambda value: 40)",
    },
}


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("case", sorted(EVIDENCE))
def test_across_modules_evidence_declines_or_passes_the_builtin(
    tmp_path: Path, case: str, flag: bool
) -> None:
    files = _package(**EVIDENCE[case])
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, files)
    _write(after, files)
    applied, declined = _refactor(after / "proj" / "pkg", parameterize_builtins=flag)
    if flag:
        assert applied > 0
        assert any("len" in parameters for parameters in _parameters(after / "proj"))
    else:
        assert applied == 0
        assert {"builtin_may_differ_by_module", "builtin_argument"} & set(declined)
        assert _python_files(after / "proj") == _python_files(before / "proj")
    assert _run(after / "outside", "drive.py", after / "proj") == _run(
        before / "outside", "drive.py", before / "proj"
    )


def test_the_flag_passes_no_builtin_every_site_reads_alike(tmp_path: Path) -> None:
    files = _package()
    _write(tmp_path, files)
    applied, _ = _refactor(tmp_path / "proj" / "pkg", parameterize_builtins=True)
    assert applied > 0
    assert _parameters(tmp_path / "proj") == [["__param_0", "name", "rows"]]
    same_module = tmp_path / "same" / "m.py"
    _write(
        tmp_path / "same",
        {"m.py": _function("first(rows, name)", "a") + "\n\n\n" + _function("s(rows, name)", "b")},
    )
    assert _refactor(same_module, parameterize_builtins=True)[0] == 1
    assert _parameters(tmp_path / "same") == [["__param_0", "name", "rows"]]


DIFFERING_IN_A_BUILTIN = """
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
"""


def test_the_flag_still_declines_blocks_that_differ_in_a_builtin(tmp_path: Path) -> None:
    _write(tmp_path, {"m.py": DIFFERING_IN_A_BUILTIN})
    applied, declined = _refactor(tmp_path / "m.py", parameterize_builtins=True)
    assert applied == 0
    assert "builtin_argument" in declined


def test_the_flag_reaches_the_engine_from_the_command_line(tmp_path: Path) -> None:
    parser = _build_parser()
    assert not DryOptions.from_namespace(parser.parse_args(["dry", "a", "b"])).parameterize_builtins
    dry = DryOptions.from_namespace(parser.parse_args(["dry", "a", "b", "--parameterize-builtins"]))
    assert dry.parameterize_builtins
    off = parser.parse_args(
        ["dry", "a", "b", "--parameterize-builtins", "--no-parameterize-builtins"]
    )
    assert not DryOptions.from_namespace(off).parameterize_builtins
    preview = parser.parse_args(["preview", "a", "--parameterize-builtins"])
    assert PreviewOptions.from_namespace(preview).parameterize_builtins
    _write(tmp_path, {"m.py": MIXED + "\n"})
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from towel.cli import main; sys.exit(main())",
            "dry",
            "m.py",
            "m.py",
            "--no-types",
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
            "--parameterize-builtins",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert _parameters(tmp_path) == [["__param_0", "len", "name", "rows"]]


# What the checker reveals for a builtin, and the annotation Towel writes.
REVEALED = [
    ("len", "def (typing.Sized) -> int", "Callable[..., int]"),
    (
        "print",
        "Overload(def (*values: object, sep: str | None =, end: str | None =, file: "
        "_typeshed.SupportsWrite[str] | None =, flush: Literal[False] =), def (*values: object, "
        "sep: str | None =, end: str | None =, file: _SupportsWriteAndFlush[str] | None =, "
        "flush: bool))",
        "Callable[..., None]",
    ),
    (
        "str",
        "Overload(def (object: object =) -> str, def (object: _collections_abc.Buffer, "
        "encoding: str =, errors: str =) -> str)",
        "type[str]",
    ),
    (
        "list",
        "Overload(def [_T] () -> list[_T], def [_T] (typing.Iterable[_T]) -> list[_T])",
        "type[list]",
    ),
    ("repr", "def (object) -> str", "Callable[[object], str]"),
    (
        "isinstance",
        "def (object, type | types.UnionType | tuple[..., ...]) -> bool",
        "Callable[..., bool]",
    ),
    ("IndexError", "def (*args: object) -> IndexError", "type[IndexError]"),
    # Overloads returning different types, and a generic, have no annotation.
    (
        "open",
        "Overload(def (file: int) -> io.TextIOWrapper, def (file: int, mode: str) -> "
        "io.BufferedReader)",
        None,
    ),
    ("abs", "def [_T] (typing.SupportsAbs[_T]) -> _T", None),
    # A name that is no builtin is left as the checker put it.
    ("measure", "def (typing.Sized) -> int", None),
]


@pytest.mark.parametrize("name, revealed, expected", REVEALED)
def test_a_builtin_parameter_is_annotated_with_what_its_body_needs(
    name: str, revealed: str, expected: Optional[str]
) -> None:
    annotation = annotation_from_revealed(
        builtin_object_revealed(name, revealed), None, False, {"Any", "Callable"}
    )
    assert (ast.unparse(annotation) if annotation is not None else None) == expected


def test_the_checkers_own_spelling_wins_wherever_it_can_be_written() -> None:
    # A local spelled as a builtin, whose exact type the host can name.
    exact = _joined_revealed(
        ["def (builtins.object) -> builtins.str"],
        None,
        False,
        fallbacks=[builtin_object_revealed("filter", "def (builtins.object) -> builtins.str")],
    )
    assert exact is not None and ast.unparse(exact) == "Callable[[object], str]"
    # The builtin ``len``, whose exact type names a protocol the host lacks.
    loosened = _joined_revealed(
        ["def (typing.Sized) -> int"],
        None,
        False,
        fallbacks=["def (*args: Any, **kwargs: Any) -> int"],
    )
    assert loosened is not None and ast.unparse(loosened) == "Callable[..., int]"


requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

TYPED_EXPORTS = """
from pkg import reports  # exports already depends on reports


def export_size(rows: list[int], name: str) -> int:
    print("exports", name)
    width = len(name)
    height = len(rows)
    label = str(width) + name
    print("measured", label)
    return width * height + 1
"""

TYPED_REPORTS = """
def report_size(rows: list[int], name: str) -> int:
    print("reports")
    width = len(name)
    height = len(rows)
    label = str(width) + name
    print("measured", label)
    return width * height + 1
"""

TYPED_TEST = """
from unittest import mock

import pkg.exports


def test_size() -> None:
    with mock.patch("pkg.exports.len", lambda value: 100, create=True):
        assert pkg.exports.export_size([1, 2], "ab") == 10001
"""


@requires_mypy
def test_under_a_strict_checker_the_builtin_parameter_is_callable(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n\n[tool.mypy]\nstrict = true\n',
            "pkg/__init__.py": "",
            "pkg/exports.py": TYPED_EXPORTS,
            "pkg/reports.py": TYPED_REPORTS,
            "tests/test_exports.py": TYPED_TEST,
        },
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from towel.cli import main; sys.exit(main())",
            "dry",
            "pkg",
            "pkg",
            "--no-format",
            "--no-interactive",
            "--progress",
            "none",
            "--cross-module",
            "--parameterize-builtins",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    (helper,) = _helpers(tmp_path / "pkg")
    annotations = {
        argument.arg: (
            argument.annotation.value
            if isinstance(argument.annotation, ast.Constant)
            else ast.unparse(argument.annotation)
        )
        for argument in helper.args.args
        if argument.annotation is not None
    }
    assert annotations["len"] == "_typing.Callable[..., int]"
