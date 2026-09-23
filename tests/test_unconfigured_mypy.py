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

"""A project that configures no mypy is checked as its own ``mypy`` checks it.

That is mypy with its defaults, run over the files the check covers. Such a
project used to be checked with ``check_untyped_defs``,
``ignore_missing_imports`` and ``explicit_package_bases`` forced on, and each
changed the verdict: dacite, clean under its own ``mypy``, was refused for 53
errors inside unannotated test functions (the audit's r4), and a test's error
went unreported where the forced module names left its imports unresolved and
the forced silence hid that. A probe is the exception, and only a probe: it
asks what type an expression has, which mypy answers with ``Any`` inside a
function without annotations unless it checks untyped defs.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
from typing import FrozenSet, Mapping, Optional, Tuple

import pytest

from towel._mypy_worker import _PROBE_CACHE, _SUPPLIED_TEXT_RECORD
from towel.type_inference import (
    CheckFailure,
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    RevealRequest,
)
from towel.unification.refactor_engine import UnificationRefactorEngine

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")

PACKAGING = '[project]\nname = "pkg"\nversion = "0"\nrequires-python = ">=3.11"\n'
ADD = "def add(a: int, b: int) -> int:\n    return a + b\n"
TWINS = """
    def first(values: list[int]) -> int:
        total = 0
        for value in values:
            total += value * 2
        return total + 1


    def second(values: list[int]) -> int:
        total = 0
        for value in values:
            total += value * 2
        return total + 2
"""
UNCHECKED_TEST = """
    from pkg.core import first


    def test_first():
        x: int = first([1])
        x = "three"
        assert x
"""
"""An error that only ``check_untyped_defs`` sees: the test function has no annotations."""


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


Verdict = Tuple[str, FrozenSet[Tuple[Optional[str], Optional[int], str]]]
"""("checked", its errors) or ("refused", the error that stopped the build)."""

_PLAIN_ERROR = re.compile(r"^(?P<path>.+?):(?:(?P<line>\d+):)? error: (?P<message>.*)$")


def _refusal(message: str) -> Tuple[Optional[str], Optional[int], str]:
    # Which of two clashing files mypy names first follows the order it was given them.
    return None, None, message.split(" (also at ")[0]


def _plain_mypy(root: Path, targets: Tuple[str, ...]) -> Verdict:
    """What ``mypy <targets>``, run in ``root`` by the interpreter Towel runs in, says."""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir=/dev/null",
            "--show-absolute-path",
            "--hide-error-codes",
            "--no-error-summary",
            *targets,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("PYTHON")},
        check=False,
    )
    assert completed.returncode in {0, 1, 2}, completed.stdout + completed.stderr
    errors = [
        match
        for match in map(_PLAIN_ERROR.match, completed.stdout.splitlines())
        if match is not None
    ]
    if completed.returncode == 2:
        return "refused", frozenset(_refusal(error.group("message")) for error in errors[:1])
    return "checked", frozenset(
        (error.group("path"), int(error.group("line")), error.group("message")) for error in errors
    )


def _towel(root: Path, analysed: Tuple[str, ...]) -> Verdict:
    result = _check(root, analysed)
    if isinstance(result, CheckFailure):
        said = re.search(r": error: (?P<message>[^\n]*)", result.reason)
        assert said is not None, result.reason
        return "refused", frozenset({_refusal(said.group("message"))})
    return "checked", frozenset((error.path, error.line, error.message) for error in result.errors)


def _check(root: Path, analysed: Tuple[str, ...]) -> CheckResult:
    oracle = MypyInferrer()
    try:
        return oracle.check_project(
            {str(root / name): (root / name).read_text(encoding="utf-8") for name in analysed}
        )
    finally:
        oracle.close()


@dataclass(frozen=True)
class Shape:
    """A project, the files a run analyses in it, and those plus what the check reaches."""

    files: Mapping[str, str]
    analysed: Tuple[str, ...]
    targets: Tuple[str, ...]


PACKAGE = ("pkg/__init__.py", "pkg/core.py")
SHADOWED_HELPER = {
    "pkg/__init__.py": "",
    "pkg/core.py": ADD,
    "helpers.py": "def make() -> str:\n    return 'root'\n",
    "tests/helpers.py": "def make() -> int:\n    return 1\n",
    "tests/test_core.py": (
        "from helpers import make\nfrom pkg.core import add\n\n\n"
        "def test_add() -> None:\n    x: str = make()\n    add(1, 2)\n"
    ),
}
"""mypy's ``import helpers`` from the test finds the module beside it, whose ``make`` is an int."""
SHAPES = {
    "an error in an unannotated test function": Shape(
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": "", "pkg/core.py": TWINS}
        | {"tests/test_core.py": UNCHECKED_TEST},
        PACKAGE,
        ("pkg", "tests/test_core.py"),
    ),
    "an error in an unannotated function of the package": Shape(
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": ""}
        | {"pkg/core.py": ADD + '\n\ndef untyped():\n    x: int = "wrong"\n'},
        PACKAGE,
        ("pkg",),
    ),
    "an attribute an unannotated method assigns, read by typed code": Shape(
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": ""}
        | {
            "pkg/core.py": (
                "class C:\n    def __init__(self):\n        self.x = 1\n\n\n"
                "def f(c: C) -> str:\n    return c.x\n"
            )
        },
        PACKAGE,
        ("pkg",),
    ),
    "an error in the package": Shape(
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": ""}
        | {"pkg/core.py": ADD + "\n\nx: str = add(1, 2)\n"},
        PACKAGE,
        ("pkg",),
    ),
    "an error in a test of a src layout": Shape(
        {"pyproject.toml": PACKAGING, "src/pkg/__init__.py": "", "src/pkg/core.py": ADD}
        | {
            "tests/test_core.py": (
                "from pkg.core import add\n\n\ndef test_add() -> None:\n    x: str = add(1, 2)\n"
            )
        },
        ("src/pkg/__init__.py", "src/pkg/core.py"),
        ("src/pkg", "tests/test_core.py"),
    ),
    # The import 1.772's changelog says was accepted, which the installed package
    # cannot run: mypy refuses to build it.
    "a src layout module imported by its path from the root": Shape(
        {"pyproject.toml": PACKAGING, "src/pkg/__init__.py": "", "src/pkg/core.py": ADD}
        | {"src/pkg/uses.py": "from src.pkg.core import add\n\nTOTAL: int = add(1, 2)\n"},
        ("src/pkg/__init__.py", "src/pkg/core.py", "src/pkg/uses.py"),
        ("src/pkg",),
    ),
    "an error through a helper module beside a test": Shape(
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": "", "pkg/core.py": ADD}
        | {"tests/helpers.py": "def make() -> int:\n    return 1\n"}
        | {
            "tests/test_core.py": (
                "from helpers import make\nfrom pkg.core import add\n\n\n"
                "def test_add() -> None:\n    x: str = make()\n    add(1, 2)\n"
            )
        },
        PACKAGE,
        ("pkg", "tests/test_core.py"),
    ),
    "an import nothing installed provides": Shape(
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": ""}
        | {"pkg/core.py": "import towel_absent_module\n\n" + ADD},
        PACKAGE,
        ("pkg",),
    ),
    "two conftest modules in directories that are not packages": Shape(
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": "", "pkg/core.py": ADD}
        | {"tests/unit/conftest.py": "from pkg.core import add\n\nX = add(1, 2)\n"}
        | {"tests/integration/conftest.py": "from pkg.core import add\n\nY = add(3, 4)\n"},
        PACKAGE,
        ("pkg", "tests/integration/conftest.py", "tests/unit/conftest.py"),
    ),
    "scripts in a directory with no packaging": Shape(
        {"app.py": "from util import double\n\n\ndef run(n: int) -> str:\n    return double(n)\n"}
        | {"util.py": "def double(n: int) -> int:\n    return n * 2\n"},
        ("app.py", "util.py"),
        (".",),
    ),
    "a helper module beside a test, and one of its name at the root": Shape(
        {"pyproject.toml": PACKAGING} | SHADOWED_HELPER,
        PACKAGE,
        ("pkg", "tests/test_core.py"),
    ),
    # Configured projects keep their own settings, the forced ones included.
    "a configuration, a helper module beside a test, and one of its name at the root": Shape(
        {"pyproject.toml": PACKAGING + "[tool.mypy]\nwarn_unused_ignores = true\n"}
        | SHADOWED_HELPER,
        PACKAGE,
        ("pkg", "tests/test_core.py"),
    ),
    "a configuration checking unannotated functions": Shape(
        {"pyproject.toml": PACKAGING + "[tool.mypy]\ncheck_untyped_defs = true\n"}
        | {"pkg/__init__.py": "", "pkg/core.py": TWINS, "tests/test_core.py": UNCHECKED_TEST},
        PACKAGE,
        ("pkg", "tests/test_core.py"),
    ),
    "a configuration naming modules from explicit bases": Shape(
        {"pyproject.toml": PACKAGING + "[tool.mypy]\nexplicit_package_bases = true\n"}
        | {"pkg/__init__.py": "", "pkg/core.py": ADD}
        | {"tests/unit/conftest.py": "from pkg.core import add\n\nX: str = add(1, 2)\n"}
        | {"tests/integration/conftest.py": "from pkg.core import add\n\nY = add(3, 4)\n"},
        PACKAGE,
        ("pkg", "tests/integration/conftest.py", "tests/unit/conftest.py"),
    ),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_check_says_what_the_projects_own_mypy_says_about_the_same_files(
    tmp_path: Path, shape: str
) -> None:
    project = tmp_path / "project"
    _write(project, SHAPES[shape].files)
    plain = _plain_mypy(project, SHAPES[shape].targets)
    assert _towel(project, SHAPES[shape].analysed) == plain


def _dry(project: Path) -> subprocess.CompletedProcess[str]:
    """The audit's reproducer: ``towel dry pkg pkg``, typed, in the project."""
    return subprocess.run(
        [sys.executable, "-c", "from towel.cli import main; main()", "dry", "pkg", "pkg"]
        + ["--no-format", "--no-interactive", "--progress", "none"],
        cwd=project,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_an_unannotated_test_the_projects_mypy_never_checks_does_not_refuse_the_run(
    tmp_path: Path,
) -> None:
    """dacite's mypy is clean, and Towel refused it for 53 errors inside untyped tests."""
    _write(
        tmp_path,
        {"pyproject.toml": PACKAGING, "pkg/__init__.py": "", "pkg/core.py": TWINS}
        | {"tests/test_core.py": UNCHECKED_TEST},
    )
    run = _dry(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    written = (tmp_path / "pkg" / "core.py").read_text(encoding="utf-8")
    assert "def __extracted_func_0(values: list[int]) -> int:" in written, written
    assert (tmp_path / "tests" / "test_core.py").read_text(encoding="utf-8") == textwrap.dedent(
        UNCHECKED_TEST
    ).lstrip()


def test_an_error_the_projects_mypy_reports_in_the_package_still_refuses_the_run(
    tmp_path: Path,
) -> None:
    broken = textwrap.dedent(TWINS).lstrip() + "\n\nWRONG: str = first([1])\n"
    _write(tmp_path, {"pyproject.toml": PACKAGING, "pkg/__init__.py": "", "pkg/core.py": broken})
    run = _dry(tmp_path)
    assert run.returncode != 0, run.stdout + run.stderr
    assert "Original project check reported 1 type error(s)" in run.stderr, run.stderr
    assert f"{tmp_path / 'pkg' / 'core.py'}: Incompatible types in assignment" in run.stderr
    assert (tmp_path / "pkg" / "core.py").read_text(encoding="utf-8") == broken


UNTYPED_PROBES = """
    class Box:
        def __init__(self):
            self.items = [1, 2, 3]


    def typed(box: Box) -> int:
        values = box.items
        return len(values)


    def untyped(box):
        local = [1, 2]
        total = sum(local)
        return total + len(box.items)
"""


def test_a_probe_sees_into_unannotated_functions_that_the_check_leaves_alone(
    tmp_path: Path,
) -> None:
    """Without ``check_untyped_defs`` every one of these probes answers ``Any``."""
    _write(tmp_path, {"pyproject.toml": PACKAGING, "pkg/__init__.py": ""})
    core = tmp_path / "pkg" / "core.py"
    source = textwrap.dedent(UNTYPED_PROBES).lstrip()
    core.write_text(source + '\n\ndef unchecked():\n    x: int = "wrong"\n', encoding="utf-8")
    lines = source.splitlines()
    typed_line = lines.index("    return len(values)") + 1
    untyped_line = lines.index("    total = sum(local)") + 1
    oracle = MypyInferrer()
    try:
        revealed = oracle.reveal(
            [
                RevealRequest(str(core), source, typed_line, "    ", ("box.items",)),
                RevealRequest(str(core), source, untyped_line, "    ", ("local",)),
            ]
        )
        checked = oracle.check_project({str(core): core.read_text(encoding="utf-8")})
    finally:
        oracle.close()
    assert {key: kind.replace("builtins.", "") for key, kind in revealed.items()} == {
        (str(core), typed_line, 0): "list[int]",
        (str(core), untyped_line, 0): "list[int]",
    }
    assert checked == CheckSuccess(), "the same oracle's check is the project's own mypy"


def _helper_signature(source: str) -> str:
    helper = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    return ast.unparse(helper).splitlines()[0]


BESIDE_AN_UNANNOTATED_FUNCTION = """
    def load() -> list[int]:
        return [1, 2, 3]


    def report() -> int:
        values = load()
        values.sort()
        total = 0
        for value in values:
            total += value * 2
        return total + 1


    def summary():
        values = load()
        values.reverse()
        total = 0
        for value in values:
            total += value * 2
        return total + 2
"""


def test_a_helper_shared_with_an_unannotated_function_keeps_the_types_probes_find(
    tmp_path: Path,
) -> None:
    """``values`` at the call in ``summary`` is ``Any`` to a probe that skips its body.

    Such a probe writes ``def __extracted_func_0(values):`` here. This is the
    signature Towel wrote while its checks still checked untyped defs.
    """
    _write(tmp_path, {"pyproject.toml": PACKAGING, "pkg/__init__.py": ""})
    core = tmp_path / "pkg" / "core.py"
    core.write_text(textwrap.dedent(BESIDE_AN_UNANNOTATED_FUNCTION).lstrip(), encoding="utf-8")
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle, reuse_existing_functions=False)
        proposal = engine.analyze_file(str(core))[0]
        written = engine.apply_refactoring(str(core), proposal)
    finally:
        oracle.close()
    assert _helper_signature(written) == "def __extracted_func_0(values: list[int]) -> int:"


@pytest.mark.parametrize("configured", [False, True])
def test_probes_that_check_more_than_the_project_build_in_a_cache_of_their_own(
    tmp_path: Path, configured: bool
) -> None:
    """mypy discards a module's cache entry built under other options.

    Probes checking untyped defs and checks not checking them, taking turns in
    one cache, rebuilt every module each time: six rounds of a revealed type, a
    subtype question and a candidate check over Towel's own source, without its
    configuration, took 12.9 s in one cache and 5.7 s in two. A configured
    project's probes use its own options and share its checks' cache.
    """
    configuration = "[tool.mypy]\nwarn_unused_ignores = true\n" if configured else ""
    _write(tmp_path, {"pyproject.toml": PACKAGING + configuration, "pkg/__init__.py": ""})
    core = tmp_path / "pkg" / "core.py"
    core.write_text(ADD, encoding="utf-8")
    cache = tmp_path / "cache"
    oracle = MypyInferrer(cache_dir=cache)
    try:
        oracle.check_project({str(core): ADD})
        revealed = oracle.reveal([RevealRequest(str(core), ADD, 2, "    ", ("a",))])
        oracle.check_project({str(core): ADD})
    finally:
        oracle.close()
    assert [kind.replace("builtins.", "") for kind in revealed.values()] == ["int"]
    probes = cache / _PROBE_CACHE if not configured else cache
    assert (cache / _PROBE_CACHE).exists() is not configured
    assert str(core) in (probes / _SUPPLIED_TEXT_RECORD).read_text(encoding="utf-8")
    if not configured:
        # The check's own entries were never written from a probe's text.
        assert not (cache / _SUPPLIED_TEXT_RECORD).exists()
