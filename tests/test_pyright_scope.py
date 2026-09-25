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

"""What a project's pyright reports on is read from its configuration, before pyright runs.

pyright reports nothing in a file its ``exclude`` or ``ignore`` names. A probe
there went unanswered: a language server waited a minute for the marker
beside it and was then given up for the rest of the run (D12), and the silence
was read as code the checker takes to be unreachable, so param's
``version.py``, which pyright ignores and mypy checks, had every proposal
declined (P2-1). Now the scope is known up front: such a file is not probed
with pyright, the marker goes where pyright answers, and the other checkers
settle what the file is.
"""

from __future__ import annotations

import importlib.util
import shutil
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

import pytest

from towel.pyright_session import (
    UNSEEN_MARKER_TIMEOUT_SECONDS,
    PyrightSession,
    pyright_scope,
)
from towel.type_inference import CombinedOracle, MypyInferrer, reports_by_each
from towel.unification.refactor_engine import UnificationRefactorEngine

requires_pyright = pytest.mark.skipif(
    shutil.which("pyright") is None and importlib.util.find_spec("pyright") is None,
    reason="pyright absent",
)
requires_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy absent")


def _files(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_exclude_and_ignore_are_outside_what_pyright_reports_on(tmp_path: Path) -> None:
    _files(tmp_path, {"pyrightconfig.json": '{"exclude": ["pkg/legacy"], "ignore": ["pkg/v*.py"]}'})
    scope = pyright_scope(tmp_path)
    assert scope.reports_on(tmp_path / "pkg" / "core.py")
    assert not scope.reports_on(tmp_path / "pkg" / "legacy" / "m.py")
    assert not scope.reports_on(tmp_path / "pkg" / "version.py")
    assert scope.reports_on(tmp_path / "pkg" / "legacy_tools.py"), "a directory, not a prefix"
    assert scope.reports_on(tmp_path / "node_modules" / "x.py"), "an exclude replaces the defaults"


def test_include_and_the_default_exclusions(tmp_path: Path) -> None:
    _files(tmp_path, {"pyproject.toml": '[tool.pyright]\ninclude = ["src"]\n'})
    scope = pyright_scope(tmp_path)
    assert scope.reports_on(tmp_path / "src" / "pkg" / "a.py")
    assert not scope.reports_on(tmp_path / "tests" / "test_a.py")
    assert not scope.reports_on(tmp_path / "src" / "__pycache__" / "a.py")
    assert not scope.reports_on(tmp_path / "src" / ".hidden" / "a.py")


def test_wildcards_and_a_base_configuration_are_read_as_pyright_reads_them(tmp_path: Path) -> None:
    _files(
        tmp_path,
        {
            "pyrightconfig.json": '{"extends": "configs/base.json", "ignore": ["**/gen_*.py"]}',
            "configs/base.json": '{"exclude": ["../build"], "ignore": ["../src"]}',
        },
    )
    scope = pyright_scope(tmp_path)
    assert not scope.reports_on(tmp_path / "build" / "lib" / "m.py"), "the base's, from its place"
    assert not scope.reports_on(tmp_path / "src" / "deep" / "gen_types.py")
    assert scope.reports_on(tmp_path / "src" / "m.py"), "the extending file's ignore wins"


def test_a_configuration_pyright_cannot_read_scopes_nothing_out(tmp_path: Path) -> None:
    _files(tmp_path, {"pyrightconfig.json": "{not json"})
    assert pyright_scope(tmp_path).reports_on(tmp_path / "anything.py")


def test_the_marker_goes_where_the_server_answers(tmp_path: Path) -> None:
    """Beside an excluded file the marker is never answered; the root or an include is."""
    _files(tmp_path, {"pyrightconfig.json": '{"include": ["pkg"], "exclude": ["pkg/legacy"]}'})
    (tmp_path / "pkg" / "legacy").mkdir(parents=True)
    session = SimpleNamespace(
        _scope=pyright_scope(tmp_path),
        _included=[tmp_path / "pkg"],
        _root=tmp_path,
        _marker=SimpleNamespace(directory=None),
    )
    choose = PyrightSession._marker_directory
    legacy = tmp_path / "pkg" / "legacy" / "m.py"
    assert choose(session, [legacy]) == tmp_path / "pkg"  # type: ignore[arg-type]
    assert choose(session, [tmp_path / "pkg" / "core.py"]) == tmp_path / "pkg"  # type: ignore
    session._marker.directory = tmp_path / "pkg" / "sub"
    assert choose(session, [legacy]) == tmp_path / "pkg" / "sub"  # type: ignore[arg-type]


@requires_pyright
def test_each_checker_reports_on_its_own_files_without_a_language_server(tmp_path: Path) -> None:
    from towel.type_inference import PyrightOracle

    _files(tmp_path, {"pyproject.toml": '[tool.pyright]\nignore = ["pkg/version.py"]\n'})
    version, core = str(tmp_path / "pkg" / "version.py"), str(tmp_path / "pkg" / "core.py")
    pyright = PyrightOracle(language_server=False)
    mypy = MypyInferrer()
    try:
        combined = CombinedOracle(mypy, [pyright])
        assert reports_by_each(combined, [version, core]) == (
            frozenset({version, core}),
            frozenset({core}),
        )
        assert pyright.reveal([]) == {} and not pyright._warmed, "no server was started"
    finally:
        combined.close()


# --- With the checkers themselves ---------------------------------------------------

VERSION = """\
    class Version:
        def __init__(self, release: tuple[int, ...], commit: str | None) -> None:
            self.release = release
            self.commit = commit

        def describe(self, prefix: str) -> str:
            parts = [str(p) for p in self.release]
            text = prefix + ".".join(parts)
            if self.commit:
                text += "+" + self.commit
            return text

        def describe_short(self, prefix: str) -> str:
            parts = [str(p) for p in self.release[:2]]
            text = prefix + ".".join(parts)
            if self.commit:
                text += "+" + self.commit
            return text
    """


def _refactored(root: Path, oracle: object) -> Sequence[str]:
    engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)  # type: ignore[arg-type]
    try:
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / "pkg"), str(root / "pkg"), progress="none"
        )
    finally:
        oracle.close()  # type: ignore[attr-defined]
    return sorted(Path(path).name for path in results)


@requires_pyright
@requires_mypy
def test_a_file_pyright_ignores_is_verified_by_mypy(tmp_path: Path) -> None:
    """P2-1, param's version.py: pyright ignores it, mypy checks it, and the change is made."""
    from towel.type_inference import PyrightOracle

    _files(
        tmp_path,
        {
            "pyproject.toml": (
                '[tool.mypy]\nfiles = ["pkg"]\n\n'
                '[tool.pyright]\ninclude = ["pkg"]\nignore = ["pkg/version.py"]\n'
            ),
            "pkg/__init__.py": "",
            "pkg/version.py": VERSION,
        },
    )
    oracle = CombinedOracle(MypyInferrer(), [PyrightOracle()])
    assert _refactored(tmp_path, oracle) == ["version.py"]
    assert "__extracted_func_0" in (tmp_path / "pkg" / "version.py").read_text(encoding="utf-8")


@requires_pyright
def test_a_directory_pyright_excludes_costs_no_wait(tmp_path: Path) -> None:
    """D12: the server used to wait a minute for a marker in the excluded directory."""
    from towel.type_inference import PyrightOracle

    _files(
        tmp_path,
        {
            "pyrightconfig.json": '{"exclude": ["pkg/legacy"]}',
            "pkg/__init__.py": "",
            "pkg/legacy/__init__.py": "",
            "pkg/legacy/version.py": VERSION,
            "pkg/core.py": "from pkg.legacy.version import Version\n\n\nV = Version((1,), None)\n",
        },
    )
    started = time.monotonic()
    oracle = PyrightOracle()
    assert _refactored(tmp_path, oracle) == ["version.py"]
    assert time.monotonic() - started < UNSEEN_MARKER_TIMEOUT_SECONDS / 2
    written = (tmp_path / "pkg" / "legacy" / "version.py").read_text(encoding="utf-8")
    helper = next(line for line in written.splitlines() if "def __extracted_func_0" in line)
    assert helper.strip() == (
        "def __extracted_func_0(self, __param_0: _typing.Any, prefix: str) -> str:"
    ), "what the sites declare and Any for the rest: nothing would check an inferred type"
