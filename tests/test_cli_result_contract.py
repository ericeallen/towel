# Copyright 2026 Eric Allen
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

"""Results can be consumed without parsing prose or mistaking cancellation for success."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import TextIO
from types import FrameType

import pytest

from towel import cli
from towel.execution_budget import BudgetExpired
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_cli_integration import DUPLICATES, invoke


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "example.py"
    source.write_text(DUPLICATES)
    return source


FLAGS = ["--no-types", "--no-format", "--progress", "none"]


@pytest.mark.parametrize(
    "command,looped", [("preview", "input"), ("dry", "input"), ("dry", "output")]
)
def test_json_reports_symlink_loops_without_resolving_the_failed_path_again(
    tmp_path: Path, command: str, looped: str
) -> None:
    source = _source(tmp_path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    target = loop if looped == "input" else source
    arguments = [command, str(target), "--json", *FLAGS]
    if command == "dry":
        arguments.extend(
            [str(loop if looped == "output" else tmp_path / "out.py"), "--no-interactive"]
        )
    result = invoke(arguments)
    assert result.status == 1
    document = json.loads(result.stdout)
    assert document["status"] == "error" and document["termination"] == "error"
    assert document["input"] == os.path.abspath(target)
    assert document["published"] is (False if command == "preview" else None)
    assert "Traceback" not in result.stderr
    assert source.read_text() == DUPLICATES
    assert loop.is_symlink() and not (tmp_path / "out.py").exists()


def test_json_failure_paths_need_no_filesystem_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)

    def inaccessible(self: Path, strict: bool = False) -> Path:
        raise OSError("path is inaccessible")

    monkeypatch.setattr(Path, "resolve", inaccessible)
    result = invoke(["preview", str(source), "--json", *FLAGS])
    assert result.status == 1
    document = json.loads(result.stdout)
    assert document["input"] == os.path.abspath(source)
    assert document["error"] == "path is inaccessible"
    assert "Traceback" not in result.stderr


def test_json_preview_is_one_document_with_a_reviewable_diff(tmp_path: Path) -> None:
    source = _source(tmp_path)
    result = invoke(["preview", str(source), "--json", *FLAGS])
    assert result.status == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert document["command"] == "preview"
    assert document["status"] == "complete"
    assert document["published"] is False
    assert document["output"] is None
    assert document["applied"] == 1
    assert "--- before/example.py" in document["diff"]
    assert document["helpers"]["helpers"]
    assert "towel-preview-" not in result.stdout + result.stderr
    assert source.read_text() == DUPLICATES


def test_summary_and_diff_modes_do_not_repeat_the_change(tmp_path: Path) -> None:
    source = _source(tmp_path)
    summary = invoke(["preview", str(source), "--summary", *FLAGS])
    assert summary.status == 0
    assert "Would apply 1 refactoring(s)." in summary.stdout
    assert "--- before/" not in summary.stdout
    assert "Helper inventory:" not in summary.stdout
    diff = invoke(["preview", str(source), "--diff", *FLAGS])
    assert diff.status == 0, diff.stderr
    assert diff.stdout.startswith("--- before/example.py\n+++ after/example.py\n")
    assert "Would apply" not in diff.stdout
    assert "Helper inventory:" not in diff.stdout
    assert source.read_text() == DUPLICATES


def test_json_dry_requires_explicit_unattended_operation(tmp_path: Path) -> None:
    source, output = _source(tmp_path), tmp_path / "output.py"
    refused = invoke(["dry", str(source), str(output), "--json", *FLAGS])
    assert refused.status == 2
    assert refused.stdout == ""
    assert "--no-interactive" in refused.stderr
    assert not output.exists()
    result = invoke(["dry", str(source), str(output), "--json", "--no-interactive", *FLAGS])
    document = json.loads(result.stdout)
    assert result.status == 0, result.stderr
    assert document["published"] is True
    assert document["output"] == str(output)
    assert document["helpers"]["target"] == str(output.parent)
    assert output.exists() and output.read_text() != DUPLICATES


@pytest.mark.parametrize("mode", ["--json", "--summary", "--diff", "--inventory"])
def test_quick_preview_result_modes_are_argument_errors(tmp_path: Path, mode: str) -> None:
    source = _source(tmp_path)
    result = invoke(["preview", str(source), "--quick", mode, *FLAGS])
    assert result.status == 2
    assert result.stdout == ""
    assert "--quick cannot be combined" in result.stderr
    assert source.read_text() == DUPLICATES


@pytest.mark.parametrize("stdin", ["", "n\n"])
def test_declining_or_closed_stdin_never_reports_success(tmp_path: Path, stdin: str) -> None:
    source, output = _source(tmp_path), tmp_path / "output.py"
    result = invoke(["dry", str(source), str(output), *FLAGS], stdin=stdin)
    assert result.status == 1
    assert "Aborted" in result.stderr
    assert not output.exists()
    assert source.read_text() == DUPLICATES


@pytest.mark.parametrize("command", ["preview", "dry", "rename-helpers"])
def test_missing_input_is_always_a_stderr_diagnostic(tmp_path: Path, command: str) -> None:
    arguments = [command, str(tmp_path / "missing.py")]
    if command == "dry":
        arguments += [str(tmp_path / "output.py"), "--no-interactive"]
    result = invoke(arguments)
    assert result.status == 1
    assert result.stdout == ""
    assert "does not exist" in result.stderr


def test_failed_json_preview_still_has_one_versioned_result(tmp_path: Path) -> None:
    result = invoke(["preview", str(tmp_path / "missing.py"), "--json"])
    document = json.loads(result.stdout)
    assert result.status == 1
    assert document["schema_version"] == 1
    assert document["status"] == "error"
    assert document["published"] is False
    assert "does not exist" in document["error"]


def test_empty_rename_inventory_is_json_too(tmp_path: Path) -> None:
    (tmp_path / "plain.py").write_text("value = 3\n")
    result = invoke(["rename-helpers", str(tmp_path), "--list", "--json"])
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)["helpers"] == []


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "not-a-number"])
def test_time_limit_rejects_unusable_values(value: str) -> None:
    result = invoke(["preview", "unused.py", "--time-limit", value])
    assert result.status == 2
    assert "positive finite number" in result.stderr


@pytest.mark.parametrize("mode", ["--summary", "--diff"])
def test_json_is_not_ambiguous_with_a_human_output_mode(mode: str) -> None:
    result = invoke(["preview", "unused.py", "--json", mode])
    assert result.status == 2
    assert "not allowed with argument" in result.stderr


@pytest.mark.parametrize("in_place", [False, True])
def test_real_process_timeout_leaves_original_and_destination_intact(
    tmp_path: Path, in_place: bool
) -> None:
    source = _source(tmp_path)
    destination = source if in_place else tmp_path / "out.py"
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from towel.cli import main; main()",
            "dry",
            str(source),
            str(destination),
            "--no-interactive",
            "--json",
            "--time-limit",
            "0.000001",
            *FLAGS,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 124, result.stderr
    document = json.loads(result.stdout)
    assert document["status"] == "incomplete"
    assert document["termination"] == "time_limit"
    assert document["published"] is False
    assert source.read_text() == DUPLICATES
    if not in_place:
        assert not destination.exists()
    assert not list(tmp_path.glob(".towel-transaction-*"))


def test_newer_python_warning_does_not_contaminate_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    # Change only the CLI's version view, not the interpreter used by AST or tools.
    monkeypatch.setattr(sys, "version_info", (3, 14, 0))
    result = invoke(["preview", str(source), "--json", *FLAGS])
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)["schema_version"] == 1
    assert "outside that range" in result.stderr


def test_timeout_during_analysis_cleans_the_private_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "out.py"
    private_files: list[Path] = []

    def slow_analysis(
        engine: UnificationRefactorEngine, paths: list[str], **kwargs: object
    ) -> list[object]:
        private_files.extend(Path(path) for path in paths)
        time.sleep(5)
        raise AssertionError("the deadline should interrupt analysis")

    monkeypatch.setattr(UnificationRefactorEngine, "analyze_files", slow_analysis)
    result = invoke(
        [
            "dry",
            str(source),
            str(destination),
            "--no-interactive",
            "--json",
            "--time-limit",
            "1",
            *FLAGS,
        ]
    )
    assert result.status == 124
    assert json.loads(result.stdout)["termination"] == "time_limit"
    assert private_files and all(not path.exists() for path in private_files)
    assert source.read_text() == DUPLICATES
    assert not destination.exists()


def test_inventory_read_failure_cannot_turn_published_json_dry_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination = _source(tmp_path), tmp_path / "out.py"

    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("inventory unavailable after publication")

    monkeypatch.setattr(cli, "_preview_inventory", unavailable)
    result = invoke(["dry", str(source), str(destination), "--no-interactive", "--json", *FLAGS])
    assert result.status == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["published"] is True and document["helpers"] is None
    assert destination.exists()
    assert "inventory unavailable" in result.stderr


def test_interrupt_after_publication_does_not_claim_that_no_output_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination = _source(tmp_path), tmp_path / "out.py"

    def interrupt_after_publication(*args: object, **kwargs: object) -> None:
        assert destination.exists()
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_write_change_sidecar", interrupt_after_publication)
    result = invoke(["dry", str(source), str(destination), "--no-interactive", "--json", *FLAGS])
    assert result.status == 130
    document = json.loads(result.stdout)
    assert document["status"] == "incomplete"
    assert document["published"] is None
    assert source.read_text() == DUPLICATES
    assert destination.read_text() != DUPLICATES


def test_timeout_while_entering_redirection_keeps_the_json_result_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination = _source(tmp_path), tmp_path / "out.py"

    class InterruptedRedirect:
        def __init__(self, target: TextIO) -> None:
            self.target = target

        def __enter__(self) -> None:
            sys.stdout = self.target
            # A signal between the assignment and return prevents the caller
            # from registering __exit__, exactly as with contextlib's redirect.
            raise BudgetExpired()

        def __exit__(self, *args: object) -> None:
            raise AssertionError("an unsuccessful __enter__ has no __exit__")

    monkeypatch.setattr(cli, "redirect_stdout", InterruptedRedirect)
    result = invoke(
        [
            "dry",
            str(source),
            str(destination),
            "--no-interactive",
            "--json",
            "--time-limit",
            "1",
            *FLAGS,
        ]
    )
    assert result.status == 124
    assert json.loads(result.stdout)["termination"] == "time_limit"
    assert "schema_version" not in result.stderr
    assert not destination.exists()


def test_timeout_restores_an_unrelated_alarm_handler_and_emits_json(tmp_path: Path) -> None:
    source, destination = _source(tmp_path), tmp_path / "out.py"
    previous = signal.getsignal(signal.SIGALRM)
    received: list[int] = []

    def unrelated(signum: int, frame: FrameType | None) -> None:
        received.append(signum)

    signal.signal(signal.SIGALRM, unrelated)
    try:
        result = invoke(
            [
                "dry",
                str(source),
                str(destination),
                "--no-interactive",
                "--json",
                "--time-limit",
                "0.000001",
                *FLAGS,
            ]
        )
        assert result.status == 124
        assert json.loads(result.stdout)["termination"] == "time_limit"
        assert "schema_version" not in result.stderr
        assert signal.getsignal(signal.SIGALRM) is unrelated
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
        signal.raise_signal(signal.SIGALRM)
        assert received == [signal.SIGALRM]
        assert not destination.exists()
    finally:
        signal.signal(signal.SIGALRM, previous)


def test_explain_locates_declined_blocks_and_does_not_leak_into_next_command(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tiny.py"
    source.write_text(
        "def first(x):\n    a = x + 1\n    b = a * 2\n    return b\n\n"
        "def second(x):\n    a = x + 1\n    b = a * 2\n    return b\n"
    )
    result = invoke(["preview", str(source), "--explain", *FLAGS])
    assert result.status == 0
    assert "unprofitable_helper" in result.stderr
    assert "tiny.py::first@" in result.stderr and "tiny.py::second@" in result.stderr
    assert "does not repay" in result.stdout
    ordinary = invoke(["preview", str(source), *FLAGS])
    assert "REJECT[" not in ordinary.stderr
