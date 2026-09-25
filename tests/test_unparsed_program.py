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
scan now reads the same files (``towel.program_files``), leaves out what
``--exclude`` names, and refuses on meeting a file it cannot parse; a run
refuses before anything is written, naming each file and the parser's
complaint, with two remedies.

The stand-in for newer syntax is PEP 810's lazy import (Python 3.15), which
every supported Python rejects; the test that uses real 3.12 syntax runs on
3.11 alone.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

import pytest

from tests.test_cli_integration import invoke
from towel import program_files as program
from towel.cli import _directory_name
from towel.consumers import consumers_of
from towel.declared_python import declared_newest_python, python_upper_bound
from towel.import_model import build_import_model
from towel.program_files import (
    parse_failure,
    program_files,
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


def test_the_program_leaves_out_tool_directories_environments_and_exclusions(
    tmp_path: Path,
) -> None:
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
    assert _relative(list(program_files(tmp_path, {"data"})), tmp_path) == [
        ".github/tool.py",
        "pkg/a.py",
    ]


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


def test_the_hand_application_index_reads_nothing_excluded(tmp_path: Path) -> None:
    _project(tmp_path, {"extras/fast.py": HAND_APPLIED, "extras/bad.py": NEWER})
    assert _first(tmp_path, ("extras",)) is None
    (tmp_path / "extras" / "bad.py").unlink()
    assert _first(tmp_path) == "numba.njit", "the control: the application is seen when read"


def test_the_namespace_scan_refuses_a_file_that_does_not_parse(tmp_path: Path) -> None:
    _project(tmp_path, {"tests/test_b.py": NEWER + PATCHES_LEN})
    with pytest.raises(UnparsedProgramError, match=r"tests/test_b\.py: line 1: "):
        scan_project_writes(tmp_path)


def test_the_namespace_scan_reads_nothing_excluded(tmp_path: Path) -> None:
    _project(tmp_path, {"tests/test_b.py": PATCHES_LEN, "tests/bad.py": NEWER + PATCHES_LEN})
    module = (tmp_path / "pkg" / "b.py").resolve()
    assert scan_project_writes(tmp_path, frozenset({"tests"})).into(module) == ()
    (tmp_path / "tests" / "bad.py").unlink()
    assert [write.name for write in scan_project_writes(tmp_path).into(module)] == ["len"]


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


def test_the_assert_rewriting_scan_reads_nothing_excluded(tmp_path: Path) -> None:
    _project(
        tmp_path, {"pytest.ini": "[pytest]\n", "plugins/marks.py": "pytest_plugins = ['pkg.a']\n"}
    )
    assert _rewrites(tmp_path) is None, "a mark pytest may or may not load: unknown"
    (tmp_path / "plugins" / "bad.py").write_text(NEWER)
    assert _rewrites(tmp_path, frozenset({"plugins"})) is False


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
    assert "--exclude tests." in text and "if it is not meant to run" in text
    refuse_unparsed_program(tmp_path / "pkg", ["tests"])  # and the remedy works


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


def test_a_file_no_exclusion_can_reach_is_named_so(tmp_path: Path) -> None:
    _project(tmp_path, {"conftest.py": NEWER, "pkg/sub/__init__.py": "", "pkg/sub/bad.py": NEWER})
    text = _refusal(tmp_path / "pkg" / "sub")
    assert "--exclude" in text and "--exclude sub" not in text and "--exclude pkg" not in text
    assert "conftest.py, pkg/sub/bad.py: --exclude cannot leave this out" in text


def test_an_exclusion_of_the_target_itself_is_refused(tmp_path: Path) -> None:
    # The scans would then read none of the code the run changes.
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
        ("tests/data", None),
        ("tests/**/hooks", None),
        ("*", None),
    ],
)
def test_exclude_takes_a_directory_name(given: str, taken: Optional[str]) -> None:
    if taken is not None:
        assert _directory_name(given) == taken
        return
    with pytest.raises(argparse.ArgumentTypeError, match="takes the name of a directory"):
        _directory_name(given)


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
