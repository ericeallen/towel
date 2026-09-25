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

"""Towel's check reports errors in exactly the files the project's own mypy run does.

The class of defect is Towel judging which files and which options count
differently from mypy itself: a per-module ``follow_imports`` read as the
global one (D4), a file named unlike its importer's import (D10), a module
named unlike mypy's walk (D9), an installed copy answering for the project's
package (D8). Each let Towel call a change clean that the project's own mypy
rejects, or refuse a project that mypy checks clean.

``test_the_check_agrees_with_mypys_own_run`` runs mypy as the project's own
``mypy`` runs it -- ``process_options`` over the configuration, then a build --
and Towel's worker over the same tree with every file changed, in this one
process, for a table of small configurations, and asserts that they report
errors in the same files and that Towel names each module mypy's run is
given as that run names it. Every file holds one error of its own, so a file
missing from either side is a judgement that differs.

The two P1s are then checked end to end, each through a real mypy worker.
"""

from __future__ import annotations

import gc
import io
import os
from pathlib import Path
import re
import sys
import sysconfig
import textwrap
import venv
from typing import Iterator, Mapping, NamedTuple, Tuple

import pytest

pytest.importorskip("mypy")

from mypy import build  # noqa: E402
from mypy.errors import CompileError  # noqa: E402
from mypy.main import process_options  # noqa: E402

from towel import _mypy_worker as worker  # noqa: E402
from towel.type_inference import CheckSuccess, MypyInferrer, RevealRequest  # noqa: E402

_ERROR_PATH = re.compile(r"^(?P<path>[^:]+):(?:\d+:)* error: ")


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


APP = {
    "app/__init__.py": "",
    "app/main.py": "from tools.gen.x import f\n\nWRONG_APP: int = f()\n",
    "tools/__init__.py": "",
    "tools/gen/__init__.py": "",
    "tools/gen/x.py": 'def f() -> str:\n    return "x"\n\n\nWRONG_X: int = "x"\n',
    "tests/test_app.py": 'import app.main\n\nWRONG_TEST: int = "t"\n',
}
"""``app`` imports ``tools.gen.x``; ``tests`` imports ``app``. Each module holds an error."""

NAMESPACE = {name: text for name, text in APP.items() if not name.startswith("tools/")} | {
    "tools/gen/x.py": APP["tools/gen/x.py"]
}
"""The same, with ``tools/gen`` a PEP 420 namespace directory (D10)."""

ACME = {
    "src/acme/shop/__init__.py": "",
    "src/acme/shop/models.py": 'class Row:\n    n: int = 0\n\n\nWRONG_MODELS: int = "m"\n',
    "src/acme/shop/report.py": ("from acme.shop.models import Row\n\nWRONG_REPORT: int = Row()\n"),
    "tests/test_report.py": 'from acme.shop.report import Row\n\nWRONG_TEST: int = "t"\n',
}
"""``acme`` is a PEP 420 namespace under ``src``, found through ``mypy_path`` (D9)."""


def _override(module: str, follow: str) -> str:
    return f'[[tool.mypy.overrides]]\nmodule = "{module}"\nfollow_imports = "{follow}"\n'


FILES = 'files = ["app"]\n'
SILENT = 'follow_imports = "silent"\n'


class Case(NamedTuple):
    """A tree, and the body of its ``[tool.mypy]``; one naming no ``files`` runs as ``mypy .``."""

    tree: Mapping[str, str]
    config: str


CASES = {
    "files, imports followed": Case(APP, FILES),
    "files, imports followed silently": Case(APP, FILES + SILENT),
    "a section follows normally under a global silent (D4)": Case(
        APP, FILES + SILENT + _override("tools.*", "normal")
    ),
    "a section skips": Case(APP, FILES + _override("tools.*", "skip")),
    "a section refuses the import": Case(APP, FILES + _override("tools.*", "error")),
    "a concrete section outranks a wildcard": Case(
        APP, FILES + _override("tools.*", "normal") + _override("tools.gen.x", "silent")
    ),
    "the whole tree": Case(APP, ""),
    "the whole tree, silently": Case(APP, SILENT),
    "an excluded module a checked one imports": Case(APP, 'exclude = ["^tools/"]\n'),
    "an excluded module nothing checked imports": Case(APP, 'exclude = ["^tests/"]\n'),
    "a namespace directory outside files (D10)": Case(NAMESPACE, FILES),
    "a namespace directory outside files, per module (D10, D4)": Case(
        NAMESPACE, FILES + SILENT + _override("tools.*", "normal")
    ),
    "explicit package bases": Case(APP, "explicit_package_bases = true\n"),
    "a namespace found through mypy_path (D9)": Case(
        ACME,
        'mypy_path = "src"\nexplicit_package_bases = true\nfiles = ["src", "tests"]\n',
    ),
    "a namespace found through mypy_path, tests excluded (D9)": Case(
        ACME, 'mypy_path = "src"\nexplicit_package_bases = true\nexclude = ["^tests/"]\n'
    ),
}


class Verdict(NamedTuple):
    """Where a run reported errors, and what it named each file it was given."""

    erroneous: frozenset[str]
    named: Mapping[str, str]


def _erroneous(messages: list[str]) -> frozenset[str]:
    return frozenset(
        os.path.abspath(match.group("path"))
        for match in map(_ERROR_PATH.match, messages)
        if match is not None
    )


def _mypys_own_run(root: Path, cache: Path, targets: Tuple[str, ...]) -> Verdict | str:
    """What the project's own ``mypy`` reports, run as its ``main`` would run it on ``targets``."""
    said = io.StringIO()
    given, options = process_options(
        [
            "--config-file",
            str(root / "pyproject.toml"),
            "--cache-dir",
            str(cache),
            "--show-absolute-path",
            "--python-executable",
            sys.executable,
            *targets,
        ],
        stdout=said,
        stderr=said,
    )
    try:
        result = build.build(sources=given, options=options)
    except CompileError as error:
        return f"refused: {error.messages}"
    return Verdict(
        _erroneous(result.errors),
        {os.path.abspath(source.path): source.module for source in given if source.path},
    )


def _towels_check(root: Path, cache: Path) -> Verdict | str:
    """What Towel's worker reports for the tree, with every file given as a changed one."""
    sources = {str(path): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.py"))}
    request = {
        "root": str(root),
        "config": str(root / "pyproject.toml"),
        "sources": sources,
        "complete": True,
        "excluded_paths": [],
        "consumers": [],
        "modules": {},
    }
    try:
        answered = worker._request(request, str(cache))
    except CompileError as error:
        return f"refused: {error.messages}"
    options = worker._options(root, str(root / "pyproject.toml"), str(cache), probe=True)[0]
    named = {
        path: worker._probed_as_the_project_names(path, text, ("", str(root)), options).module
        for path, text in sources.items()
    }
    return Verdict(_erroneous(answered.messages), named)


@pytest.fixture(scope="module")
def caches(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Tuple[Path, Path]]:
    """One cache for mypy's own runs and one for Towel's, shared by every case.

    A build in this process sets the collector's thresholds for it, so they are
    put back for the tests that follow.
    """
    thresholds = gc.get_threshold()
    root = tmp_path_factory.mktemp("caches")
    yield root / "mypy", root / "towel"
    gc.set_threshold(*thresholds)


@pytest.mark.parametrize("name", list(CASES))
def test_the_check_agrees_with_mypys_own_run(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caches: Tuple[Path, Path],
) -> None:
    case = CASES[name]
    root = tmp_path / "project"
    _write(root, {**case.tree, "pyproject.toml": "[tool.mypy]\n" + case.config})
    monkeypatch.chdir(root)
    own = _mypys_own_run(root, caches[0], () if "files =" in case.config else (".",))
    assert not isinstance(own, str), own  # every case is one mypy itself checks
    towel = _towels_check(root, caches[1])
    assert not isinstance(towel, str), towel
    files = sorted(str(path) for path in root.rglob("*.py"))
    assert {path: path in towel.erroneous for path in files} == {
        path: path in own.erroneous for path in files
    }
    assert {path: towel.named[path] for path in own.named} == dict(own.named)


# --- The P1s, end to end through a real mypy worker ------------------------------


D4 = {
    "pyproject.toml": "[tool.mypy]\n" + FILES + SILENT + _override("tools.*", "normal"),
    "app/__init__.py": "",
    "app/main.py": "from tools.gen.x import double\n\ndouble([1])\n",
    "tools/__init__.py": "",
    "tools/gen/__init__.py": "",
    "tools/gen/x.py": "def double(values: list[int]) -> list[int]:\n    return values + values\n",
}


def test_d4_an_error_in_a_module_its_own_section_follows_is_not_dropped(tmp_path: Path) -> None:
    """The audit's D4: the helper ``__param_0: list[int] | str`` passed Towel and not mypy."""
    _write(tmp_path, D4)
    changed = str(tmp_path / "tools" / "gen" / "x.py")
    oracle = MypyInferrer()
    try:
        result = oracle.check_project(
            {changed: "def double(values: list[int] | str) -> None:\n    values + values\n"}
        )
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess), result
    assert {error.path for error in result.errors} == {changed}
    assert any("Unsupported operand types" in error.message for error in result.errors)


def _interpreter_with_a_stale_copy(root: Path) -> Path:
    """A Python whose installed ``shop`` is a stale 1.0, and which can run mypy."""
    environment = root / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = Path(
        sysconfig.get_path("purelib", vars={"base": str(environment), "platbase": str(environment)})
    )
    here = {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}
    (purelib / "towel-test-mypy.pth").write_text("\n".join(sorted(here)) + "\n")
    _write(
        purelib,
        {
            "shop/__init__.py": "",
            "shop/py.typed": "",
            "shop/util.py": "DEFAULT: object = [1, 2, 3]\n\n\ndef describe(values: object) -> str:\n"
            '    return "values"\n',
        },
    )
    return python


def test_d8_a_tests_only_target_is_checked_against_the_tree_not_a_stale_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit's D8: ``DEFAULT`` was revealed ``object`` from the stale 1.0 and not ``list[int]``."""
    project = tmp_path / "project"
    _write(
        project,
        {
            "pyproject.toml": "[tool.mypy]\n",
            "src/shop/__init__.py": "",
            "src/shop/py.typed": "",
            "src/shop/util.py": "DEFAULT: list[int] = [1, 2, 3]\n\n\n"
            'def describe(values: list[int]) -> str:\n    return "values"\n',
            "tests/test_shop.py": "from shop.util import DEFAULT, describe\n\n\n"
            "def test_a() -> None:\n    describe(DEFAULT)\n",
        },
    )
    monkeypatch.setattr(sys, "executable", str(_interpreter_with_a_stale_copy(tmp_path)))
    test = project / "tests" / "test_shop.py"
    source = test.read_text()
    oracle = MypyInferrer()
    try:
        revealed = oracle.reveal([RevealRequest(str(test), source, 5, "    ", ("DEFAULT",))])
        checked = oracle.check_project(
            {
                str(test): source.replace(
                    "describe(DEFAULT)", "value: object = DEFAULT\n    describe(value)"
                )
            }
        )
    finally:
        oracle.close()
    assert revealed[(str(test), 5, 0)].replace("builtins.", "") == "list[int]"
    assert isinstance(checked, CheckSuccess), checked
    assert [error.message for error in checked.errors] == [
        'Argument 1 to "describe" has incompatible type "object"; expected "list[int]"'
    ]
