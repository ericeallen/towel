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

"""The warm pyright must answer exactly as the command line does.

Two properties here were live defects during development, and each would have
let an unsound refactoring through rather than merely slowing one down.

A change is judged by the files that depend on it. Pyright's in-memory overlays
reanalyze only the overlaid file, so a helper that breaks a consumer reads as
clean through them; the copy the server watches must therefore be a real one,
changed by real writes it is told about.

A file's diagnostics are stated once and then not repeated. A settle that
reported only what arrived during it would forget every error the project
already had, so a candidate checked later would look clean on a project that
never was.
"""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import signal
import subprocess
import sys
import threading
from typing import Any, Dict, List, Mapping, Sequence

import pytest

from towel import pyright_session, type_inference
from towel.pyright_session import Diagnostic
from towel.source_files import PROBE_PREFIX
from towel.type_inference import CheckSuccess, PyrightOracle


def _project(root: Path, *, returns: str = "int", value: str = "1") -> Path:
    """A provider and consumers that declare the provider's result type."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyrightconfig.json").write_text(
        '{"typeCheckingMode":"strict","include":["pkg"]}', encoding="utf-8"
    )
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    provider = package / "provider.py"
    provider.write_text(f"def make() -> {returns}:\n    return {value}\n", encoding="utf-8")
    for index in range(3):
        (package / f"consumer_{index}.py").write_text(
            "from pkg.provider import make\n\n\n"
            f"def use_{index}() -> int:\n    value: int = make()\n    return value\n",
            encoding="utf-8",
        )
    return provider


def _consumer_errors(errors: Sequence[object]) -> int:
    return sum(1 for error in errors if "consumer" in getattr(error, "path", ""))


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
def test_a_helper_that_breaks_its_consumers_is_seen(tmp_path: Path, language_server: bool) -> None:
    """Both paths must report the consumers, not just the file that changed."""
    provider = _project(tmp_path)
    breaking = 'def make() -> str:\n    return "x"\n'
    oracle = PyrightOracle(language_server=language_server)
    try:
        clean = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
        broken = oracle.check_project({str(provider): breaking})
    finally:
        oracle.close()
    assert isinstance(clean, CheckSuccess) and isinstance(broken, CheckSuccess)
    assert _consumer_errors(clean.errors) == 0
    assert _consumer_errors(broken.errors) == 3, broken.errors
    assert provider.read_text(encoding="utf-8").endswith("return 1\n"), "the project was edited"


def test_a_candidate_is_judged_against_errors_the_project_already_had(tmp_path: Path) -> None:
    """Diagnostics stated before the first candidate must not be forgotten."""
    provider = _project(tmp_path, returns="str", value='"x"')
    oracle = PyrightOracle()
    try:
        # The first call absorbs the project's existing errors; the second
        # changes nothing and must still see them.
        first = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
        second = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
    finally:
        oracle.close()
    assert isinstance(first, CheckSuccess) and isinstance(second, CheckSuccess)
    assert _consumer_errors(first.errors) == 3
    assert _consumer_errors(second.errors) == 3, "a silent second call reported a clean project"


def test_the_warm_and_cold_paths_agree(tmp_path: Path) -> None:
    """Whatever the mechanism, the verdict is the checker's."""
    provider = _project(tmp_path)
    candidate = 'def make() -> str:\n    return "x"\n'
    verdicts = []
    for language_server in (True, False):
        oracle = PyrightOracle(language_server=language_server)
        try:
            result = oracle.check_project({str(provider): candidate})
        finally:
            oracle.close()
        assert isinstance(result, CheckSuccess)
        verdicts.append(sorted((Path(e.path).name, e.message) for e in result.errors))
    assert verdicts[0] == verdicts[1]


def test_a_stale_candidate_does_not_survive_into_the_next_check(tmp_path: Path) -> None:
    """The copy shows the candidate under test, never the one before it."""
    provider = _project(tmp_path)
    good = provider.read_text(encoding="utf-8")
    oracle = PyrightOracle()
    try:
        broken = oracle.check_project({str(provider): 'def make() -> str:\n    return "x"\n'})
        assert isinstance(broken, CheckSuccess)
        assert _consumer_errors(broken.errors) == 3
        # Naming no replacement at all must restore the project's own source.
        restored = oracle.check_project({str(provider): good})
        assert isinstance(restored, CheckSuccess)
        assert _consumer_errors(restored.errors) == 0, restored.errors
        again = oracle.check_project({})
        assert isinstance(again, CheckSuccess)
        assert _consumer_errors(again.errors) == 0, again.errors
    finally:
        oracle.close()


def test_a_session_that_cannot_start_still_yields_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable server falls back to the command line rather than failing."""
    provider = _project(tmp_path)
    monkeypatch.setattr(
        "towel.type_inference._pyright_langserver_command",
        lambda: ["/nonexistent/towel-no-such-langserver"],
    )
    oracle = PyrightOracle()
    try:
        result = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess)
    assert _consumer_errors(result.errors) == 0


@pytest.mark.parametrize("language_server", [True, False], ids=["session", "command-line"])
def test_a_candidate_is_judged_against_the_project_as_it_now_stands(
    tmp_path: Path, language_server: bool
) -> None:
    """An in-place run changes the project and supplies only what the next candidate alters.

    The warm copy once restored every unsupplied file to the bytes it first saw,
    so a valid follow-up was rejected and a breaking candidate accepted.
    """
    provider = _project(tmp_path)
    consumer = provider.with_name("consumer_0.py")
    oracle = PyrightOracle(language_server=language_server)
    try:
        baseline = oracle.check_project({str(consumer): consumer.read_text(encoding="utf-8")})
        # An applied refactoring gives the provider a helper, on disk.
        provider.write_text(
            "def make() -> int:\n    return extra()\n\n\ndef extra() -> int:\n    return 1\n",
            encoding="utf-8",
        )
        uses_the_helper = oracle.check_project(
            {
                str(consumer): "from pkg.provider import extra\n\n\n"
                "def use_0() -> int:\n    value: int = extra()\n    return value\n"
            }
        )
        # The provider's result type then changes on disk under an unchanged consumer.
        provider.write_text('def make() -> str:\n    return "x"\n', encoding="utf-8")
        now_broken = oracle.check_project({str(consumer): consumer.read_text(encoding="utf-8")})
    finally:
        oracle.close()
    assert isinstance(baseline, CheckSuccess)
    assert isinstance(uses_the_helper, CheckSuccess)
    assert isinstance(now_broken, CheckSuccess)
    assert baseline.errors == ()
    assert uses_the_helper.errors == (), uses_the_helper.errors
    assert _consumer_errors(now_broken.errors) == 3, now_broken.errors


def _process_tree(root_pid: int) -> list[int]:
    found, frontier = [root_pid], [root_pid]
    while frontier:
        listed = subprocess.run(
            ["pgrep", "-P", str(frontier.pop())], capture_output=True, text=True, check=False
        )
        children = [int(pid) for pid in listed.stdout.split()]
        found += children
        frontier += children
    return found


@pytest.mark.skipif(shutil.which("pgrep") is None, reason="needs pgrep to find the server's tree")
def test_a_server_slow_to_begin_is_waited_for_not_read_as_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silence before the server has started on a change is not a verdict.

    The server is stopped just before the candidate is shown and resumed well
    after the quiet period, which is what a loaded machine looks like from here.
    A settle that ended on silence alone reported this breaking candidate clean.
    """
    provider = _project(tmp_path)
    oracle = PyrightOracle()
    original = type_inference._WarmProject.diagnostics
    stalled: list[int] = []

    def stall_then_ask(
        warm: type_inference._WarmProject, replacements: Mapping[str, str]
    ) -> Dict[str, List[Diagnostic]]:
        if stalled:
            return original(warm, replacements)
        stalled.extend(_process_tree(warm._session._process.pid))
        for pid in stalled:
            os.kill(pid, signal.SIGSTOP)

        def resume_server() -> None:
            for pid in stalled:
                os.kill(pid, signal.SIGCONT)

        resume = threading.Timer(1.5, resume_server)
        resume.start()
        try:
            return original(warm, replacements)
        finally:
            resume.join()

    try:
        clean = oracle.check_project({str(provider): provider.read_text(encoding="utf-8")})
        assert isinstance(clean, CheckSuccess) and clean.errors == ()
        monkeypatch.setattr(type_inference._WarmProject, "diagnostics", stall_then_ask)
        broken = oracle.check_project({str(provider): 'def make() -> str:\n    return "x"\n'})
    finally:
        for pid in stalled:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        oracle.close()
    assert stalled, "the server was never stalled, so this proved nothing"
    assert isinstance(broken, CheckSuccess)
    assert _consumer_errors(broken.errors) == 3, broken.errors


def test_the_marker_is_no_part_of_the_verdict_or_the_project(tmp_path: Path) -> None:
    provider = _project(tmp_path)
    oracle = PyrightOracle()
    try:
        broken = oracle.check_project({str(provider): 'def make() -> str:\n    return "x"\n'})
    finally:
        oracle.close()
    assert isinstance(broken, CheckSuccess)
    assert not [error for error in broken.errors if PROBE_PREFIX in error.path]
    assert not list(tmp_path.rglob(f"{PROBE_PREFIX}*"))


def test_a_directory_the_server_does_not_analyze_falls_back_to_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No marker will ever be answered there; waiting longer would not change that."""
    _project(tmp_path)
    outside = tmp_path / "scripts"
    outside.mkdir()
    script = outside / "tool.py"
    script.write_text("VALUE: int = 1\n", encoding="utf-8")
    monkeypatch.setattr(pyright_session, "UNSEEN_MARKER_TIMEOUT_SECONDS", 2.0)
    oracle = PyrightOracle()
    try:
        result = oracle.check_project({str(script): "VALUE: int = 2\n"})
        assert oracle._server is None, "the session should have been abandoned"
    finally:
        oracle.close()
    assert isinstance(result, CheckSuccess)
    assert result.errors == ()


def test_a_session_that_cannot_start_leaves_no_process_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A constructor that raises binds no object, so it must clean up itself."""
    started: List[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def remember(*args: Any, **kwargs: Any) -> "subprocess.Popen[bytes]":
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr("towel.pyright_session.subprocess.Popen", remember)
    monkeypatch.setattr(pyright_session, "START_TIMEOUT_SECONDS", 0.05)
    command = type_inference._pyright_langserver_command()
    if command is None:
        pytest.skip("pyright is absent")
    with pytest.raises(pyright_session.SessionFailure):
        pyright_session.PyrightSession(command, tmp_path, sys.executable)
    assert started, "no server was started, so this proved nothing"
    for process in started:
        assert process.poll() is not None, "the server outlived the failed constructor"
