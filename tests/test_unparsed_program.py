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

"""A file of the program that does not parse here refuses the run: it may run on a newer Python.

Round 4 of the 1.772 audit (P1-2, P1-3) found Towel on 3.11 skipping a file
of a 3.12 project as one that cannot run. The file applied a recompiling
decorator by hand, or patched ``len`` into a module in a test, and Towel,
not having seen it, changed what the program computes. Every whole-program
scan now reads the same files (``towel.program_files``) and refuses on
meeting a file it cannot parse; a run refuses before anything is written,
naming each file and the parser's complaint, with two remedies that each
clear it. What ``--exclude`` names is still read, as evidence; only a file
it names that does not parse is taken for no part of the program.

The stand-in for newer syntax is PEP 810's lazy import (Python 3.15), which
every supported Python rejects; the test that uses real 3.12 syntax runs on
3.11 alone.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

import pytest

from tests.test_cli_integration import invoke
from towel import program_files as program
from towel.cli import _excluded_name
from towel.consumers import consumers_of
from towel.declared_python import declared_newest_python, python_upper_bound
from towel.import_model import build_import_model
from towel.program_files import (
    excluded_by,
    parse_failure,
    program_files,
    refuse_unparsed_file,
    refuse_unparsed_program,
    unparsed_program_files,
)
from towel.project_layout import find_project_root
from towel.unification import assert_rewriting
from towel.unification.decorator_reach import ModuleSource, decorator_refusal
from towel.unification.exceptions import UnparsedProgramError
from towel.unification.import_graph import ImportGraphCache
from towel.unification.namespace_writes import scan_project_writes
from towel.unification.refactor_engine import UnificationRefactorEngine

NEWER = "lazy import json  # PEP 810: Python 3.15\n"
"""A line no supported Python parses, standing for syntax newer than the running one."""

KERNELS = """\
def first(n):
    total = 0
    for i in range(n):
        total += i * 2
    total = total + 1
    return total


def second(n):
    total = 0
    for i in range(n):
        total += i * 2
    total = total + 1
    return -total
"""

HAND_APPLIED = "import numba\nfrom pkg import kernels\nfast_first = numba.njit(kernels.first)\n"

DESCRIBE = """\
def describe_{name}(items):
    count = len(items)
    doubled = count * 2
    label = "n=" + str(doubled)
    print("{name}", label)
    return label
"""

PATCHES_LEN = (
    "from unittest import mock\n"
    "from pkg.b import describe_b\n"
    "def test_patched():\n"
    '    with mock.patch("pkg.b.len", create=True, return_value=100):\n'
    "        assert describe_b([1]) == 'n=200'\n"
)


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _project(root: Path, extra: Mapping[str, str], pyproject: str = "") -> Path:
    """A package ``pkg`` whose two functions share a block, and ``extra`` files beside it."""
    return _write(
        root,
        {
            "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n' + pyproject,
            "pkg/__init__.py": "",
            "pkg/kernels.py": KERNELS,
            "pkg/a.py": DESCRIBE.format(name="a"),
            "pkg/b.py": DESCRIBE.format(name="b"),
            **extra,
        },
    )


def _relative(paths: List[Path], root: Path) -> List[str]:
    return sorted(path.relative_to(root).as_posix() for path in paths)


def test_the_stand_in_for_newer_syntax_does_not_parse_here() -> None:
    # When a supported Python accepts it, pick syntax newer still.
    with pytest.raises(SyntaxError):
        ast.parse(NEWER)


# -- the files of the program ---------------------------------------------------


def test_the_program_leaves_out_tool_directories_and_environments(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/a.py": "",
            "pkg/data/fixture.py": "",
            "data/fixture.py": "",
            ".github/tool.py": "",
            ".git/hook.py": "",
            "build/lib/pkg/a.py": "",
            "node_modules/n.py": "",
            "env/pyvenv.cfg": "",
            "env/lib/site.py": "",
            "vendor/site-packages/v.py": "",
        },
    )
    assert _relative(list(program_files(tmp_path)), tmp_path) == [
        ".github/tool.py",
        "data/fixture.py",
        "pkg/a.py",
        "pkg/data/fixture.py",
    ]
    # What --exclude names is read like the rest; it only says which unparsed files to pass over.
    assert excluded_by(tmp_path / "pkg/data/fixture.py", tmp_path, {"data"})
    assert excluded_by(tmp_path / "pkg/a.py", tmp_path, {"a.py"})
    assert not excluded_by(tmp_path / "pkg/a.py", tmp_path, {"data", "b.py"})


@pytest.mark.parametrize(
    ("data", "complaint"),
    [
        (b"x = 1\n", None),
        (b"def f(:\n    pass\n", "line 1: "),
        (NEWER.encode(), "line 1: "),
        (b'def f():\n    return "caf\xe9"\n', None),
        (b"# -*- coding: lala -*-\nx = 1\n", None),
        (b"x = 1\0\n", "null bytes"),
    ],
    ids=["parses", "syntax error", "newer syntax", "does not decode", "no such codec", "null byte"],
)
def test_what_the_parser_says_of_a_file(
    tmp_path: Path, data: bytes, complaint: Optional[str]
) -> None:
    path = tmp_path / "m.py"
    path.write_bytes(data)
    failure = parse_failure(path)
    if complaint is None:
        assert failure is None
    else:
        assert failure is not None and complaint in failure.complaint


def test_the_program_is_read_whole_and_the_target_with_it(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {
            "tests/test_b.py": NEWER,
            "pkg/build/generated.py": NEWER,
            "docs/data/bad.py": NEWER,
        },
    )
    found = unparsed_program_files(tmp_path / "pkg", frozenset({"data"}))
    # The target's own build directory, which the scans skip, is still analyzed.
    assert _relative([failure.path for failure in found], tmp_path.resolve()) == [
        "pkg/build/generated.py",
        "tests/test_b.py",
    ]


# -- each whole-program scan ----------------------------------------------------


def _first(root: Path, excluded: Tuple[str, ...] = ()) -> Optional[str]:
    """The decorator refusal of ``pkg.kernels.first``, as the hand-application index reads it."""
    path = root / "pkg" / "kernels.py"
    source = path.read_text()
    tree = ast.parse(source)
    definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    refusal = decorator_refusal(
        definition, ModuleSource(str(path), source, tree), ImportGraphCache(excluded_names=excluded)
    )
    return None if refusal is None else refusal.decorator


def test_the_hand_application_index_refuses_a_file_that_does_not_parse(tmp_path: Path) -> None:
    _project(tmp_path, {"pkg/fast.py": NEWER + HAND_APPLIED})
    with pytest.raises(UnparsedProgramError, match=r"pkg/fast\.py: line 1: "):
        _first(tmp_path)


def test_the_hand_application_index_still_reads_what_is_excluded(tmp_path: Path) -> None:
    # An excluded directory is left unchanged, not unseen; its unparsed file is passed over.
    _project(tmp_path, {"extras/fast.py": HAND_APPLIED, "extras/bad.py": NEWER})
    assert _first(tmp_path, ("extras",)) == "numba.njit"
    assert _first(tmp_path, ("bad.py",)) == "numba.njit"
    with pytest.raises(UnparsedProgramError, match=r"extras/bad\.py"):
        _first(tmp_path)


def test_the_namespace_scan_refuses_a_file_that_does_not_parse(tmp_path: Path) -> None:
    _project(tmp_path, {"tests/test_b.py": NEWER + PATCHES_LEN})
    with pytest.raises(UnparsedProgramError, match=r"tests/test_b\.py: line 1: "):
        scan_project_writes(tmp_path)


def test_the_namespace_scan_still_reads_what_is_excluded(tmp_path: Path) -> None:
    _project(tmp_path, {"tests/test_b.py": PATCHES_LEN, "tests/bad.py": NEWER + PATCHES_LEN})
    module = (tmp_path / "pkg" / "b.py").resolve()
    written = scan_project_writes(tmp_path, frozenset({"tests"})).into(module)
    assert [write.name for write in written] == ["len"]
    with pytest.raises(UnparsedProgramError, match=r"tests/bad\.py"):
        scan_project_writes(tmp_path)


def test_a_scan_that_cannot_parse_what_this_module_can_still_refuses(tmp_path: Path) -> None:
    """Under ``-W error`` an invalid escape fails a scan's parse; the check ignores warnings."""
    path = tmp_path / "m.py"
    path.write_text('x = "\\d"\n')
    refuse_unparsed_file(path, tmp_path)  # It parses here.
    with pytest.raises(UnparsedProgramError, match=r"m\.py: line 1: invalid escape"):
        refuse_unparsed_file(
            path, tmp_path, error=SyntaxError("invalid escape", ("m.py", 1, 5, ""))
        )
    refuse_unparsed_file(path, tmp_path, {"m.py"}, SyntaxError("invalid escape"))  # excluded


def _rewrites(root: Path, excluded: frozenset[str] = frozenset()) -> Optional[bool]:
    assert_rewriting._SETUPS.clear()
    return assert_rewriting.rewrites_asserts(
        str(root / "pkg" / "a.py"), find_project_root, excluded
    )


def test_the_assert_rewriting_scan_refuses_a_file_that_does_not_parse(tmp_path: Path) -> None:
    _project(
        tmp_path,
        {"pytest.ini": "[pytest]\n", "plugins/marks.py": NEWER + "pytest_plugins = ['pkg.a']\n"},
    )
    with pytest.raises(UnparsedProgramError, match=r"plugins/marks\.py: line 1: "):
        _rewrites(tmp_path)


def test_the_assert_rewriting_scan_still_reads_what_is_excluded(tmp_path: Path) -> None:
    _project(
        tmp_path, {"pytest.ini": "[pytest]\n", "plugins/marks.py": "pytest_plugins = ['pkg.a']\n"}
    )
    (tmp_path / "plugins" / "bad.py").write_text(NEWER + "pytest_plugins = ['pkg.b']\n")
    assert _rewrites(tmp_path, frozenset({"plugins"})) is None, "a mark pytest may load: unknown"
    (tmp_path / "plugins" / "marks.py").unlink()
    assert _rewrites(tmp_path, frozenset({"bad.py"})) is False, "the control: nothing marks pkg.a"


def test_a_file_that_does_not_parse_is_no_consumer_and_refuses_the_run_first(
    tmp_path: Path,
) -> None:
    # The checker, running here too, could not build it; the run refuses before it asks.
    _project(tmp_path, {"consumer.py": "import pkg.a\n", "newer.py": NEWER + "import pkg.a\n"})
    found = consumers_of(tmp_path, ["pkg"], module_name=lambda path: path.stem)
    assert _relative([Path(path) for path in found], tmp_path.resolve()) == ["consumer.py"]
    with pytest.raises(UnparsedProgramError, match=r"newer\.py"):
        refuse_unparsed_program(tmp_path / "pkg")


def test_the_import_model_spells_nothing_to_or_from_a_file_it_cannot_parse(
    tmp_path: Path,
) -> None:
    _project(
        tmp_path, {"pkg/fast.py": NEWER + "from pkg import kernels\n", "run.py": "import pkg.a\n"}
    )
    model = build_import_model(tmp_path)
    fast, kernels, a = (tmp_path / "pkg" / name for name in ("fast.py", "kernels.py", "a.py"))
    assert model.spelling(fast, kernels) is None and model.spelling(kernels, fast) is None
    assert model.spelling(a, kernels) is not None, "the control: parsed siblings are spelled"
    excluded = build_import_model(tmp_path, excluded_names=["pkg"])
    assert excluded.module_name(kernels) is None


# -- the refusal ------------------------------------------------------------------


def _refusal(target: Path, excluded: Tuple[str, ...] = ()) -> str:
    with pytest.raises(UnparsedProgramError) as refused:
        refuse_unparsed_program(target, excluded)
    return str(refused.value)


def test_the_refusal_names_the_file_the_complaint_and_both_remedies(tmp_path: Path) -> None:
    _project(tmp_path, {"tests/test_b.py": NEWER + PATCHES_LEN})
    text = _refusal(tmp_path / "pkg")
    assert "  tests/test_b.py: line 1: invalid syntax" in text
    assert "Run Towel on a Python that parses it: the newest Python" in text
    assert "--exclude test_b.py." in text and "if it is not meant to run" in text
    refuse_unparsed_program(tmp_path / "pkg", ["test_b.py"])  # and the remedy works
    refuse_unparsed_program(tmp_path / "pkg", ["tests"])  # and so does its directory


@pytest.mark.parametrize(
    ("pyproject", "remedy"),
    [
        (
            'classifiers = ["Programming Language :: Python :: 3.14",'
            ' "Programming Language :: Python :: 3.15"]\n',
            "Python 3.15, the newest the project declares it supports.",
        ),
        ('requires-python = ">=3.11,<3.16"\n', "Python 3.15, the newest the project declares"),
        (
            'requires-python = ">=3.15,<3.16"\n',
            "Python 3.15, the newest the project declares it supports; it requires Python 3.15",
        ),
        ('requires-python = ">=3.15"\n', "the newest Python, since the project declares no newest"),
        ('requires-python = "<3.11"\n', "if there is one: the newest the project declares"),
    ],
    ids=["classifiers", "upper bound", "both bounds", "requires newer", "nothing newer"],
)
def test_the_refusal_names_the_newest_python_the_project_declares(
    tmp_path: Path, pyproject: str, remedy: str
) -> None:
    _project(tmp_path, {"pkg/fast.py": NEWER}, pyproject)
    assert remedy in _refusal(tmp_path)


def test_a_file_at_the_project_root_is_excluded_by_its_name(tmp_path: Path) -> None:
    """unidecode keeps a Python 2 ``benchmark.py`` at its root, which no directory holds."""
    _project(tmp_path, {"benchmark.py": "print 'fast'\n"})
    text = _refusal(tmp_path)
    assert "  benchmark.py: line 1: Missing parentheses in call to 'print'" in text
    assert "leave it out: --exclude benchmark.py. Towel then refactors no directory" in text
    refuse_unparsed_program(tmp_path, ["benchmark.py"])


@pytest.mark.parametrize(
    ("files", "target", "suggested"),
    [
        ({"conftest.py": NEWER}, "pkg", ["conftest.py"]),
        ({"pkg/sub/__init__.py": "", "pkg/sub/bad.py": NEWER}, "pkg/sub", ["bad.py"]),
        (
            {"data/cases/a.py": NEWER, "data/cases/b.py": NEWER, "data/c.py": NEWER},
            ".",
            ["c.py", "cases"],
        ),
        ({"pkg/fast.py": NEWER}, ".", ["fast.py"]),
        ({"pkg/fast.py": NEWER}, "pkg/kernels.py", ["fast.py"]),
        ({"pkg/sub/__init__.py": NEWER}, ".", ["sub"]),
        ({"pkg/__init__.py": NEWER}, "pkg", ["__init__.py"]),
    ],
    ids=[
        "root file",
        "in the target",
        "shared directory",
        "package code",
        "beside a file target",
        "initializer",
        "target initializer",
    ],
)
def test_every_suggested_exclusion_clears_the_refusal(
    tmp_path: Path, files: Dict[str, str], target: str, suggested: List[str]
) -> None:
    _project(tmp_path, files)
    goal = tmp_path / target
    text = _refusal(goal)
    assert " ".join(f"--exclude {name}" for name in suggested) + "." in text
    assert "no --exclude leaves out" not in text
    refuse_unparsed_program(goal, suggested)


def test_the_file_being_refactored_is_never_excluded(tmp_path: Path) -> None:
    _project(tmp_path, {"pkg/broken.py": NEWER})
    target = tmp_path / "pkg" / "broken.py"
    assert "pkg/broken.py: the file the run was given to refactor" in _refusal(target)
    assert "fix it, or run Towel on a Python that parses it" in _refusal(target, ("broken.py",))


def test_an_exclusion_of_the_target_itself_is_refused(tmp_path: Path) -> None:
    # The run would change what it was told to leave alone.
    _project(tmp_path, {})
    for target in (tmp_path / "pkg", tmp_path / "pkg" / "kernels.py"):
        with pytest.raises(ValueError, match=r"--exclude pkg would leave out"):
            refuse_unparsed_program(target, ["pkg"])
    refuse_unparsed_program(tmp_path, ["pkg"])  # Inside the target it only narrows the run.


@pytest.mark.parametrize(
    ("specifier", "newest"),
    [
        ("<3.13", (3, 12)),
        ("<3.13.0", (3, 12)),
        ("<3.12.5", (3, 12)),
        ("<=3.12", (3, 12)),
        ("==3.12.*", (3, 12)),
        ("===3.12.1", (3, 12)),
        ("~=3.12.1", (3, 12)),
        ("~3.12", (3, 12)),
        (">=3.9,<3.14,<3.13", (3, 12)),
        ("~=3.9", None),
        ("^3.9", None),
        ("<4", None),
        ("==3.*", None),
        (">=3.9", None),
        ("any", None),
    ],
)
def test_the_upper_bound_of_a_python_requirement(
    specifier: str, newest: Optional[Tuple[int, int]]
) -> None:
    assert python_upper_bound(specifier) == newest


def test_setup_cfg_classifiers_declare_the_newest_python(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "setup.cfg": "[metadata]\nclassifiers =\n"
            "    Programming Language :: Python :: 3.13\n"
            "    Programming Language :: Python :: 3.12\n"
        },
    )
    assert declared_newest_python(tmp_path) == (3, 13)


@pytest.mark.parametrize(
    ("given", "taken"),
    [
        ("data", "data"),
        ("tests/", "tests"),
        ("benchmark.py", "benchmark.py"),
        ("tests/data", "pass --exclude data"),
        ("tests/data/bad.py", "pass --exclude bad.py"),
        ("tests/**/hooks", "pass --exclude hooks"),
        ("*.py", None),
    ],
)
def test_exclude_takes_a_directory_or_file_name(given: str, taken: Optional[str]) -> None:
    if taken is not None and "pass" not in taken:
        assert _excluded_name(given) == taken
        return
    with pytest.raises(argparse.ArgumentTypeError) as refused:
        _excluded_name(given)
    assert "takes the name of a directory or a file" in str(refused.value)
    assert taken is None or taken in str(refused.value)


# -- the command line, in every mode --------------------------------------------


MODES: Dict[str, List[str]] = {
    "dry": ["dry", "--no-types"],
    "dry --cross-module": ["dry", "--no-types", "--cross-module"],
    "dry typed": ["dry", "--types"],
    "preview": ["preview"],
    "preview --cross-module": ["preview", "--cross-module"],
}


@pytest.mark.parametrize("mode", sorted(MODES))
def test_every_mode_refuses_before_writing_anything(tmp_path: Path, mode: str) -> None:
    project = _project(tmp_path / "proj", {"pkg/fast.py": NEWER + HAND_APPLIED})
    before = {path: path.read_bytes() for path in sorted(project.rglob("*")) if path.is_file()}
    command, *flags = MODES[mode]
    output = tmp_path / "out"
    places = [str(project / "pkg"), str(output)] if command == "dry" else [str(project / "pkg")]
    asked = ["--no-interactive"] if command == "dry" else []
    ran = invoke([command, *places, *flags, *asked, "--progress", "none"])
    assert ran.status == 1, ran.stderr
    assert "pkg/fast.py: line 1: invalid syntax" in ran.stderr
    assert "Run Towel on a Python that parses it" in ran.stderr
    assert "--exclude" in ran.stderr
    assert not output.exists()
    assert {path: path.read_bytes() for path in before} == before


def _refactored(root: Path, target: str, excluded: Tuple[str, ...], cross_module: bool) -> int:
    engine = UnificationRefactorEngine(
        min_lines=3, excluded_directories=excluded, cross_module_helpers=cross_module
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / target), str(root / target), progress="none"
        )
    return sum(applied for applied, _ in results.values())


def test_an_excluded_test_suite_still_declines_a_change_its_patch_would_notice(
    tmp_path: Path,
) -> None:
    """``--exclude tests`` keeps the tests unchanged; their ``mock.patch`` of ``len`` still counts."""
    files = {
        "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n',
        "pkg/__init__.py": "",
        "pkg/a.py": DESCRIBE.format(name="a"),
        "pkg/b.py": DESCRIBE.format(name="b"),
    }
    patched = _write(tmp_path / "patched", {**files, "tests/test_b.py": PATCHES_LEN})
    assert _refactored(patched, "pkg", ("tests",), cross_module=True) > 0
    assert "count = len(items)" in (patched / "pkg" / "b.py").read_text(), "len stays in pkg.b"
    control = _write(tmp_path / "control", files)
    assert _refactored(control, "pkg", ("tests",), cross_module=True) > 0
    assert "len(items)" not in (control / "pkg" / "b.py").read_text(), "the control moves it"


def test_an_excluded_hand_application_still_declines_the_function_it_decorates(
    tmp_path: Path,
) -> None:
    kernels = tmp_path / "hand" / "pkg" / "kernels.py"
    hand = _project(tmp_path / "hand", {"extras/fast.py": HAND_APPLIED})
    (hand / "pkg" / "a.py").unlink()
    (hand / "pkg" / "b.py").unlink()
    assert _refactored(hand, ".", ("extras",), cross_module=False) == 0
    assert kernels.read_text() == KERNELS
    (hand / "extras" / "fast.py").unlink()
    assert _refactored(hand, ".", ("extras",), cross_module=False) == 1, "the control"


@pytest.mark.parametrize("excluded", ["tests", "test_b.py"])
def test_an_excluded_file_that_does_not_parse_no_longer_refuses(
    tmp_path: Path, excluded: str
) -> None:
    project = _project(tmp_path / "proj", {"tests/test_b.py": NEWER + PATCHES_LEN})
    output = tmp_path / "out"
    ran = invoke(
        [
            "dry",
            str(project),
            str(output),
            "--no-types",
            "--no-interactive",
            "--progress",
            "none",
            "--exclude",
            excluded,
        ]
    )
    assert ran.status == 0, ran.stderr
    assert "Refusing" not in ran.stderr
    assert (output / "tests" / "test_b.py").read_text() == NEWER + PATCHES_LEN


def test_a_root_file_named_in_the_refusal_is_cleared_on_the_command_line(tmp_path: Path) -> None:
    project = _project(tmp_path / "proj", {"benchmark.py": NEWER})
    arguments = ["preview", str(project), "--progress", "none"]
    refused = invoke(arguments)
    assert refused.status == 1 and "--exclude benchmark.py." in refused.stderr
    assert invoke([*arguments, "--exclude", "benchmark.py"]).status == 0


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="3.12 parses the PEP 701 f-string")
def test_a_3_12_file_refuses_a_3_11_run(tmp_path: Path) -> None:
    """The audit's P1-2 as it was found: the project requires 3.12, Towel runs on 3.11."""
    fast = 'MODE = "fast"\nBANNER = f"{"mode" if MODE else "none"}"\n' + HAND_APPLIED
    project = _project(tmp_path / "proj", {"pkg/fast.py": fast}, 'requires-python = ">=3.12"\n')
    ran = invoke(["dry", str(project), str(tmp_path / "out"), "--no-types", "--no-interactive"])
    assert ran.status == 1
    assert "on Python 3.11, which Towel is running on" in ran.stderr
    assert "pkg/fast.py: line 2: f-string: expecting '}'" in ran.stderr
    assert "it requires Python 3.12 or newer" in ran.stderr
    assert not (tmp_path / "out").exists()


def test_a_library_analysis_refuses_at_its_first_pair(tmp_path: Path) -> None:
    """``analyze_files`` has no up-front check, but every pair consults the hand-application index."""
    project = _project(tmp_path, {"pkg/fast.py": NEWER + HAND_APPLIED})
    engine = UnificationRefactorEngine(min_lines=3)
    with pytest.raises(UnparsedProgramError, match=r"pkg/fast\.py"):
        engine.analyze_files([str(project / "pkg" / "kernels.py")], progress="none")


def test_the_towel_repository_parses_on_this_python() -> None:
    """The suite refactors files inside this repository, so none of its files may refuse it.

    A fixture in newer syntax is stored as ``.pynew`` (``tests.hostile_execution``).
    """
    root = Path(__file__).resolve().parents[1]
    assert [failure.describe(root) for failure in program.unparsed_program_files(root)] == []


def test_a_file_that_changes_during_the_run_is_parsed_again(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("x = 1\n")
    assert parse_failure(path) is None
    path.write_text(NEWER + "# longer now\n")
    assert parse_failure(path) is not None
