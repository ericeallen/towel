"""Exercise installed CLI dispatch against real files and the real refactoring engine."""

import io
import json
import sys
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest

from towel import cli

BODY = "    a = x + 1\n    b = a * 2\n    c = b + 3\n    return c\n"
DUPLICATES = "def first(x):\n" + BODY + "\ndef second(x):\n" + BODY
HELPER = "def __extracted_func_0(x):\n    return x * 2\n\nRESULT = __extracted_func_0(7)\n"


class CliResult(NamedTuple):
    status: int
    stdout: str
    stderr: str


def invoke(
    arguments: Sequence[str], stdin: str = "", entry: Callable[[], None] = cli.main
) -> CliResult:
    """Replace only process I/O boundaries; parser, handlers and engine run normally."""
    stdout, stderr = io.StringIO(), io.StringIO()
    status = 0
    with (
        patch.object(sys, "argv", ["towel", *arguments]),
        patch.object(sys, "stdin", io.StringIO(stdin)),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        try:
            entry()
        except SystemExit as exit_result:
            status = (
                exit_result.code
                if isinstance(exit_result.code, int)
                else int(bool(exit_result.code))
            )
    return CliResult(status, stdout.getvalue(), stderr.getvalue())


def evaluate_functions(source: str) -> list[object]:
    namespace: dict[str, object] = {}
    exec(compile(source, "<cli-integration>", "exec"), namespace)
    results: list[object] = []
    for name in ("first", "second"):
        function = namespace[name]
        assert callable(function)
        results.extend(function(value) for value in (-3, 0, 7))
    return results


@pytest.mark.parametrize(
    "arguments",
    [[], ["--help"], ["dry", "--help"], ["preview", "--help"], ["rename-helpers", "--help"]],
)
def test_help_exits_successfully(arguments: list[str]) -> None:
    result = invoke(arguments)
    assert result.status == 0
    assert "usage:" in result.stdout
    assert not result.stderr


def test_version_matches_installed_distribution() -> None:
    result = invoke(["--version"])
    assert result.status == 0
    assert result.stdout.strip() == f"towel {version('code-towel')}"


@pytest.mark.parametrize("directory", [False, True])
def test_preview_finds_duplicates_without_changing_files(tmp_path: Path, directory: bool) -> None:
    source = tmp_path / "module.py"
    source.write_text(DUPLICATES)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = invoke(["preview", str(tmp_path if directory else source)])
    assert result.status == 0
    assert "REFACTORING OPPORTUNITIES" in result.stdout
    assert "first" in result.stdout and "second" in result.stdout
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    # The human preview shows each call site's original block and generated call.
    assert "Call sites (- before / + after):" in result.stdout
    assert "        - " in result.stdout and "        + " in result.stdout


def test_preview_no_duplicates_is_a_successful_read_only_result(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def lone():\n    return 3\n")
    result = invoke(["preview", str(source)])
    assert result.status == 0
    assert "No duplicates found" in result.stdout
    assert source.read_text() == "def lone():\n    return 3\n"


@pytest.mark.parametrize("directory", [False, True])
def test_dry_creates_refactored_output_with_same_runtime_results(
    tmp_path: Path, directory: bool
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "module.py"
    source.write_text(DUPLICATES)
    (source_root / "data.json").write_text('{"keep": true}\n')
    destination = tmp_path / ("output" if directory else "output.py")
    result = invoke(
        [
            "dry",
            str(source_root if directory else source),
            str(destination),
            "--non-interactive",
            "--max-iterations",
            "1",
            "--progress",
            "none",
        ]
    )
    assert result.status == 0, result
    output = destination / "module.py" if directory else destination
    changed = output.read_text()
    assert "Applied 1 refactoring" in result.stdout
    assert changed != DUPLICATES
    assert evaluate_functions(changed) == evaluate_functions(DUPLICATES) == [-1, 5, 19, -1, 5, 19]
    assert source.read_text() == DUPLICATES
    if directory:
        assert (destination / "data.json").read_bytes() == (source_root / "data.json").read_bytes()


@pytest.mark.parametrize("command", ["preview", "dry", "rename-helpers"])
def test_missing_input_has_failure_status_and_no_output(tmp_path: Path, command: str) -> None:
    missing, output = tmp_path / "missing.py", tmp_path / "output.py"
    arguments = [command, str(missing)]
    if command == "dry":
        arguments += [str(output), "--non-interactive"]
    result = invoke(arguments)
    assert result.status == 1
    assert "does not exist" in result.stdout + result.stderr
    assert not output.exists()


def test_helper_listing_and_filters_are_read_only(tmp_path: Path) -> None:
    selected = tmp_path / "selected.py"
    selected.write_text(HELPER)
    (tmp_path / "other.py").write_text(HELPER.replace("func_0", "func_1"))
    result = invoke(
        [
            "rename-helpers",
            str(tmp_path),
            "--list",
            "--file",
            "selected.py",
            "--function",
            "__extracted_func_0",
        ]
    )
    assert result.status == 0
    assert "selected.py" in result.stdout and "__extracted_func_0" in result.stdout
    assert "other.py" not in result.stdout and "__extracted_func_1" not in result.stdout
    assert selected.read_text() == HELPER


@pytest.mark.parametrize("dry_run", [False, True])
def test_interactive_helper_prompt_accepts_manual_markdown_json(
    tmp_path: Path, dry_run: bool
) -> None:
    source = tmp_path / "helpers.py"
    source.write_text(HELPER)
    arguments = ["rename-helpers", str(tmp_path), "--llm", "claude"]
    if dry_run:
        arguments.append("--dry-run")
    response = '```json\n{"helpers.py:__extracted_func_0": "double_value"}\n```\n'
    result = invoke(arguments, stdin=response)
    assert result.status == 0
    assert "Copy the following prompt" in result.stdout
    assert "return x * 2" in result.stdout
    assert "double_value" in result.stdout
    if dry_run:
        assert "DRY RUN" in result.stdout
        assert source.read_text() == HELPER
    else:
        namespace: dict[str, object] = {}
        exec(source.read_text(), namespace)
        assert namespace["RESULT"] == 14
        assert "double_value" in namespace and "__extracted_func_0" not in namespace


@pytest.mark.parametrize("dry_run", [False, True])
def test_helper_rename_file_runs_through_dispatch(tmp_path: Path, dry_run: bool) -> None:
    source = tmp_path / "helpers.py"
    source.write_text(HELPER)
    mapping = tmp_path / "rename.json"
    mapping.write_text(json.dumps({"__extracted_func_0": "double_value"}))
    arguments = ["rename-helpers", str(tmp_path), "--rename-file", str(mapping)]
    if dry_run:
        arguments.append("--dry-run")
    result = invoke(arguments)
    assert result.status == 0
    assert "double_value" in result.stdout
    assert source.read_text() == (
        HELPER if dry_run else HELPER.replace("__extracted_func_0", "double_value")
    )


@pytest.mark.parametrize(
    "response, diagnostic",
    [
        ("nonsense", "Error parsing JSON"),
        ("[]", "must be a JSON object"),
        ("", "No response provided"),
    ],
)
def test_invalid_interactive_json_reports_problem_without_writing(
    tmp_path: Path, response: str, diagnostic: str
) -> None:
    source = tmp_path / "helpers.py"
    source.write_text(HELPER)
    result = invoke(["rename-helpers", str(tmp_path)], stdin=response)
    assert diagnostic in result.stdout
    assert result.status == (1 if response else 0)
    assert source.read_text() == HELPER


@pytest.mark.parametrize("contents", ["not json", "[]"])
def test_invalid_rename_file_has_failure_status(tmp_path: Path, contents: str) -> None:
    source, mapping = tmp_path / "helpers.py", tmp_path / "renames.json"
    source.write_text(HELPER)
    mapping.write_text(contents)
    result = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping)])
    assert result.status == 1
    assert "Error" in result.stdout
    assert source.read_text() == HELPER
