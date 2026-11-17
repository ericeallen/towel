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

"""Complete preview predicts dry, including refusals and later passes, without publishing.

The real CLI and fixed-point drivers run throughout. Capturing the private
destination before it disappears lets these tests compare its actual bytes
with dry's output; counting structural proposals alone would miss validation,
formatting, follow-ups and the final cold confirmation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_checker_failure_reporting import _Oracle
from tests.test_cli_integration import LITERAL_DUPLICATES as DUPLICATES
from tests.test_cli_integration import invoke
from tests.test_progress_modes import _make_fixture, _two_round_project
from tests.typed_fixtures import requires_mypy
from towel import cli
from towel.diagnostics import LOG
from towel.filesystem import StagedProject, staged_project
from towel.formatting import FormattingChangedCode
from towel.type_inference import CheckFailure, CheckSuccess, TypeDiagnostic, TypeOracle
from towel.unification import fixed_point
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine


def _contents(target: Path) -> dict[str, bytes]:
    if target.is_file():
        return {"<file>": target.read_bytes()}
    return {
        str(path.relative_to(target)): path.read_bytes()
        for path in target.rglob("*")
        # Only dry publishes the sidecar for a later rename-helpers command.
        if path.is_file() and path.name != ".towel-helpers.json"
    }


def _snapshot(root: Path) -> dict[str, tuple[int, int, int, bytes | str]]:
    """Reads may change atime; no entry, content, inode, mode or mtime may change."""
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode,
            path.lstat().st_mtime_ns,
            path.lstat().st_ino,
            (
                str(path.readlink())
                if path.is_symlink()
                else path.read_bytes() if path.is_file() else b""
            ),
        )
        for path in [root, *root.rglob("*")]
    }


def _capture_preview(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, dict[str, bytes]]]:
    """Observe confirmed staged bytes before preview removes its private stage."""
    completed: list[tuple[Path, dict[str, bytes]]] = []
    content = cli._preview_content

    def capture(
        engine: UnificationRefactorEngine, options: cli.DryOptions, stage: StagedProject
    ) -> cli._PreviewContent:
        completed.append((stage.target, _contents(stage.target)))
        return content(engine, options, stage)

    monkeypatch.setattr(cli, "_preview_content", capture)
    return completed


def _counted(count: int, reason: str, stdout: str) -> bool:
    """Whether the run's reasons list ``reason`` once, ``count`` times: ``  1  <sentence> [reason]``."""
    return bool(re.search(rf"^ +{count}  .*\[{re.escape(reason)}\]$", stdout, re.MULTILINE))


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("cap, count", [(0, 2), (1, 1)])
def test_complete_preview_matches_dry_bytes_and_requested_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, cap: int, count: int
) -> None:
    """Both drivers must reach the same result; a cap deliberately stops before fixed point."""
    root = tmp_path / "project"
    _make_fixture(root)
    (root / "pyproject.toml").write_text("[project]\nname = 'preview-fixture'\nversion = '0'\n")
    (root / "data.txt").write_text("keep this too\n")
    target = root if directory else root / "sample.py"
    before = _snapshot(root)
    completed = _capture_preview(monkeypatch)
    flags = ["--no-types", "--no-format", "--progress", "none", "--max-refactorings", str(cap)]
    preview = invoke(["preview", str(target), *flags])
    assert preview.status == 0, preview
    assert f"Would apply {count} refactoring(s)" in preview.stdout
    # 2.0 says why a run stopped only when it stopped at the cap, in words.
    stopped = "Stopped at the limit of 1 refactoring (--max-refactorings)."
    assert (stopped in preview.stdout) == bool(cap)
    assert "Termination:" not in preview.stdout
    assert "Applied " not in preview.stdout
    assert "towel-preview-" not in preview.stdout + preview.stderr
    assert "--- before/" in preview.stdout and "+++ after/" in preview.stdout
    assert _snapshot(root) == before
    assert len(completed) == 1
    temporary, predicted = completed[0]
    assert not temporary.parent.exists(), "The complete preview removes its private output"
    output = tmp_path / ("output" if directory else "output.py")
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert dry.status == 0, dry
    assert f"Applied {count} refactoring(s)" in dry.stdout
    written = _contents(output)
    if directory:
        assert written.pop("data.txt") == b"keep this too\n"
        assert "data.txt" not in predicted
    assert written == predicted
    assert _snapshot(root) == before


def test_preview_passes_every_shared_option_to_dry() -> None:
    """Adding a dry option must not silently give its complete preview different semantics."""
    parser = cli._build_parser()
    flags = [
        "--min-lines",
        "4",
        "--max-parameters",
        "7",
        "--max-pairs",
        "50",
        "--max-refactorings",
        "2",
        "--parameterize-builtins",
        "--cross-module",
        "--exclude",
        "vendor",
        "--no-types",
        "--no-format",
        "--progress",
        "detail",
        "--quiet",
    ]
    preview = cli.PreviewOptions.from_namespace(parser.parse_args(["preview", "src", *flags]))
    dry = cli.DryOptions.from_namespace(
        parser.parse_args(["dry", "src", "out", "--no-interactive", *flags])
    )
    assert preview.dry_options("out") == dry
    assert preview.quiet and dry.quiet
    defaults = cli.PreviewOptions.from_namespace(parser.parse_args(["preview", "src"]))
    assert defaults.types and defaults.format and not defaults.quick
    assert not defaults.quiet
    assert defaults.max_refactorings == 0


@pytest.mark.parametrize("directory", [False, True])
def test_preview_shows_final_helper_definitions_and_dry_inventory(
    tmp_path: Path, directory: bool
) -> None:
    """A list of replaced calls alone cannot show the helper's behavior or its final bindings."""
    root = tmp_path / "input"
    root.mkdir()
    # This inventory test needs a fresh helper; plain whole functions now redirect.
    source = DUPLICATES.replace("def first(", "@staticmethod\ndef first(").replace(
        "def second(", "@staticmethod\ndef second("
    )
    (root / "module.py").write_text(source)
    target = root if directory else root / "module.py"
    flags = ["--no-types", "--no-format", "--progress", "none"]
    result = invoke(["preview", str(target), "--interactive", "--inventory", *flags])
    assert result.status == 0, result
    assert "--- before/module.py" in result.stdout
    assert "+++ after/module.py" in result.stdout
    assert "+def __extracted_func" in result.stdout
    inventory, _ = json.JSONDecoder().raw_decode(result.stdout.split("Helper inventory:\n", 1)[1])
    output_root = tmp_path / "output"
    if not directory:
        output_root.mkdir()
    output = output_root if directory else output_root / "module.py"
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert dry.status == 0, dry
    expected = cli.helper_inventory(
        output_root,
        cli._find_extracted_helpers(output, None, None),
        changes_by_helper=cli._read_change_sidecar(cli._change_sidecar_path(output)),
    )
    expected["target"] = str(root)
    assert inventory == expected
    assert inventory["helpers"][0]["parameters"]
    assert "towel-preview-" not in result.stdout
    assert (root / "module.py").read_text() == source
    compatible = invoke(["preview", str(target), "--no-interactive", "--inventory", *flags])
    assert compatible.stdout == result.stdout and compatible.status == 0


@pytest.mark.parametrize("strict", [False, True])
def test_complete_preview_keeps_cross_module_followups_and_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strict: bool
) -> None:
    """Re-analysis after rewriting the shared host must still find its other extraction."""
    root = tmp_path / "project"
    _two_round_project(root)
    (root / "pyproject.toml").write_text("[project]\nname = 'preview-fixture'\nversion = '0'\n")
    (root / "vendor").mkdir()
    (root / "vendor" / "ignored.py").write_text("this is intentionally invalid Python!!!\n")
    completed = _capture_preview(monkeypatch)
    flags = [
        "--strict" if strict else "--no-strict",
        "--no-types",
        "--no-format",
        "--cross-module",
        "--parameterize-builtins",
        "--exclude",
        "vendor",
        "--progress",
        "detail",
    ]
    preview = invoke(["preview", str(root), *flags])
    assert preview.status == 0, preview
    assert "Would apply 4 refactoring(s)" in preview.stdout
    assert "Discovered 2 proposal(s)" in preview.stderr
    assert "Discovered 1 proposal(s)" in preview.stderr
    assert "Termination:" not in preview.stdout and "Stopped at the limit" not in preview.stdout
    assert "towel-preview-" not in preview.stdout + preview.stderr
    output = tmp_path / "output"
    dry = invoke(["dry", str(root), str(output), "--no-interactive", *flags])
    assert dry.status == 0, dry
    written = _contents(output)
    assert written.pop("vendor/ignored.py") == b"this is intentionally invalid Python!!!\n"
    previewed = dict(completed[0][1])
    if strict:
        # Strict retains excluded source in the whole-program stage; relaxed
        # mode omits the invalid fixture. Both produce the same selected changes.
        assert previewed.pop("vendor/ignored.py") == b"this is intentionally invalid Python!!!\n"
    assert written == previewed


def test_quick_is_explicitly_partial_and_does_not_run_checkers_or_formatters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only --quick retains the old single-analysis display, with no applicability promise."""
    source = tmp_path / "module.py"
    source.write_text(DUPLICATES)
    forbidden = Mock(side_effect=AssertionError("quick must only analyze"))
    for name in ("_execute_dry", "_type_oracle", "_generated_code_formatter", "_file_finisher"):
        monkeypatch.setattr(cli, name, forbidden)
    result = invoke(["preview", str(source), "--quick", "--progress", "none"])
    assert result.status == 0, result
    assert "PARTIAL STRUCTURAL ANALYSIS" in result.stdout
    assert "not an upper bound on eventual extractions" in result.stdout
    assert "Candidates may fail dry's validation" in result.stdout
    assert "Found 1 refactoring opportunit" in result.stdout
    assert "Call sites (- before / + after):" in result.stdout
    assert "To apply these" not in result.stdout
    forbidden.assert_not_called()
    assert source.read_text() == DUPLICATES


@pytest.mark.parametrize("directory", [False, True])
def test_complete_preview_excludes_a_structural_proposal_the_checker_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    """Inject a diagnostic to test scheduling, not mypy inference: quick sees it; dry refuses it."""
    root = tmp_path / "project"
    root.mkdir()
    source = root / "module.py"
    source.write_text(DUPLICATES)
    target = root if directory else source
    oracles: list[_Oracle] = []

    def select(_path: Path) -> TypeOracle:
        def refuse(sources: Mapping[str, str]) -> CheckSuccess:
            path = next(iter(sources))
            return CheckSuccess((TypeDiagnostic(path, "invented: candidate-only error", 1),))

        oracle = _Oracle(refuse)
        oracles.append(oracle)
        return oracle

    monkeypatch.setattr(cli, "_type_oracle", select)
    flags = ["--no-format", "--progress", "none"]
    quick = invoke(["preview", str(target), "--quick", *flags])
    assert "Found 1 refactoring opportunit" in quick.stdout
    assert not oracles
    preview = invoke(["preview", str(target), *flags])
    output = tmp_path / ("out" if directory else "out.py")
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert preview.status == dry.status == 0, (preview, dry)
    assert "Would apply 0 refactoring(s)" in preview.stdout
    assert _counted(1, "refused by the type checker", preview.stdout)
    assert _counted(1, "refused by the type checker", dry.stdout)
    assert "Call sites (- before / + after):" not in preview.stdout
    assert all(oracle.checks > 1 for oracle in oracles)
    assert _contents(output) == _contents(target)


@requires_mypy
def test_complete_preview_uses_original_config_and_whole_project_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A package-only input still sees its strict config and consumer through engine staging."""
    root = tmp_path / "project"
    package = root / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    source = DUPLICATES.replace("():", "() -> int:")
    (package / "module.py").write_text(source)
    config = "[tool.mypy]\nstrict = true\n[tool.black]\nline-length = 88\n"
    (root / "pyproject.toml").write_text(config)
    (root / "consumer.py").write_text("from pkg.module import first\nvalue: int = first()\n")
    before = _snapshot(root)
    observed: list[Path] = []

    @contextmanager
    def stage(
        origin_root: Path,
        origin_target: Path,
        output: Path,
        *,
        limit: int,
        preview: bool = False,
        notice: Callable[[str], None] | None = None,
    ) -> Iterator[StagedProject]:
        assert origin_root == root and origin_target == package
        with staged_project(
            origin_root, origin_target, output, limit=limit, preview=preview, notice=notice
        ) as staged:
            assert (staged.root / "pyproject.toml").read_text() == config
            assert (staged.root / "consumer.py").read_bytes() == (root / "consumer.py").read_bytes()
            observed.append(staged.root)
            yield staged

    monkeypatch.setattr(fixed_point, "staged_project", stage)
    completed = _capture_preview(monkeypatch)
    preview = invoke(["preview", str(package), "--progress", "none"])
    assert preview.status == 0, preview
    assert "Would apply 1 refactoring(s)" in preview.stdout
    assert "-> int:" in completed[0][1]["module.py"].decode()
    assert _snapshot(root) == before
    output = tmp_path / "out"
    dry = invoke(["dry", str(package), str(output), "--no-interactive", "--progress", "none"])
    assert dry.status == 0, dry
    assert _contents(output) == completed[0][1]
    assert len(observed) == 2 and all(not path.exists() for path in observed)
    assert _snapshot(root) == before


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("failure", ["baseline", "final"])
def test_complete_preview_and_dry_both_fail_closed_when_checking_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, failure: str
) -> None:
    """A failed original or final check cannot produce a successful preview or leave output."""
    root = tmp_path / "project"
    root.mkdir()
    source = root / "module.py"
    source.write_text(DUPLICATES)
    target = root if directory else source
    before = _snapshot(root)
    closed: list[Mock] = []
    finals: list[tuple[str, ...]] = []
    private_stages: list[Path] = []

    @contextmanager
    def capture(
        origin_root: Path,
        origin_target: Path,
        output: Path,
        *,
        limit: int,
        preview: bool = False,
        notice: Callable[[str], None] | None = None,
    ) -> Iterator[StagedProject]:
        with staged_project(
            origin_root, origin_target, output, limit=limit, preview=preview, notice=notice
        ) as stage:
            private_stages.append(stage.root)
            yield stage

    def select(_path: Path) -> TypeOracle:
        oracle = Mock(spec=TypeOracle)
        oracle.check_project.return_value = CheckFailure("test checker unavailable")
        closed.append(oracle)
        return oracle

    def fail_final(_engine: UnificationRefactorEngine, paths: Sequence[str]) -> None:
        finals.append(tuple(paths))
        raise RefactoringError(f"test final check refused {paths[0]}")

    monkeypatch.setattr(fixed_point, "staged_project", capture)
    if failure == "baseline":
        monkeypatch.setattr(cli, "_type_oracle", select)
    else:
        monkeypatch.setattr(
            UnificationRefactorEngine, "confirm_run_with_a_cold_checker", fail_final
        )
    flags = ["--no-format", "--progress", "none"] + (["--no-types"] if failure == "final" else [])
    preview = invoke(["preview", str(target), *flags])
    output = tmp_path / ("out" if directory else "out.py")
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert preview.status == dry.status == 1, (preview, dry)
    message = "test checker unavailable" if failure == "baseline" else "test final check refused"
    assert message in preview.stderr and message in dry.stderr
    assert "Would apply" not in preview.stdout
    assert "towel-preview-" not in preview.stderr
    assert str(root) in preview.stderr if failure == "final" else True
    assert len(private_stages) == (0 if failure == "baseline" else 2)
    assert all(not path.parent.exists() for path in private_stages)
    assert not output.exists()
    assert _snapshot(root) == before
    for oracle in closed:
        oracle.close.assert_called_once_with()
    assert len(closed if failure == "baseline" else finals) == 2


@pytest.mark.parametrize("directory", [False, True])
def test_formatter_refusals_are_not_shown_as_applicable_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    """The same proposal is declined by dry and preview when its selected formatter fails."""
    root = tmp_path / "project"
    root.mkdir()
    source = root / "module.py"
    # A custom formatter must be reached without an unrelated ownership refusal.
    body = "    a = 1\n    b = 2\n    c = 3\n    return a * b + c\n"
    source.write_text("def first():\n" + body + "\ndef second():\n" + body)
    target = root if directory else source
    selections: list[Path] = []

    def fail(_source: str) -> str:
        raise FormattingChangedCode("test formatter failed")

    def select(path: Path):
        selections.append(path)
        return fail

    monkeypatch.setattr(cli, "_generated_code_formatter", select)
    monkeypatch.setattr(cli, "_file_finisher", lambda path, *, sort_imports: None)
    flags = ["--no-types", "--progress", "none"]
    preview = invoke(["preview", str(target), *flags])
    output = tmp_path / ("out" if directory else "out.py")
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert preview.status == dry.status == 0, (preview, dry)
    assert "Would apply 0 refactoring(s)" in preview.stdout
    assert _counted(1, "the formatter changed its code or failed", preview.stdout)
    assert _counted(1, "the formatter changed its code or failed", dry.stdout)
    assert selections == [target, target]
    assert _contents(output) == _contents(target)


def test_preview_path_translation_keeps_error_and_warning_paths_useful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a failure after the temporary destination exists must name the surviving input."""
    source = tmp_path / "module.py"
    source.write_text(DUPLICATES)
    execute = cli._execute_dry

    def warn_then_fail(options: cli.DryOptions, *, preview: bool = False) -> None:
        execute(replace(options, types=False, format=False), preview=preview)
        LOG.warning("test diagnostic: %s", options.output)
        raise OSError(5, "test I/O failure", options.output)

    monkeypatch.setattr(cli, "_execute_dry", warn_then_fail)
    result = invoke(["preview", str(source), "--progress", "none"])
    assert result.status == 1
    assert str(source) in result.stderr
    assert "towel-preview-" not in result.stderr
    assert source.read_text() == DUPLICATES


def test_preview_omits_bulk_payloads_and_still_matches_dry_for_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime item 4: filtering the preview copy must not turn it into in-place semantics."""
    root = tmp_path / "project"
    root.mkdir()
    original = tmp_path / "outside.py"
    original.write_text(DUPLICATES)
    (root / "module.py").hardlink_to(original)
    (root / "pyproject.toml").write_text("")
    (root / "data.bin").write_bytes(b"payload")
    for name in (".git", ".venv", "node_modules"):
        directory = root / name
        directory.mkdir()
        (directory / "bulk.bin").write_bytes(b"unanalysed")
    (root / ".venv" / "pyvenv.cfg").write_text("")
    before = _snapshot(root)
    completed = _capture_preview(monkeypatch)
    flags = ["--no-types", "--no-format", "--progress", "none"]
    preview = invoke(["preview", str(root), *flags])
    assert preview.status == 0, preview
    assert "Would apply 1 refactoring(s)" in preview.stdout
    assert len(completed) == 1
    predicted = completed[0][1]
    assert sorted(predicted) == ["module.py", "pyproject.toml"]
    output = tmp_path / "out"
    dry = invoke(["dry", str(root), str(output), "--no-interactive", *flags])
    assert dry.status == 0, dry
    assert (output / "module.py").read_bytes() == predicted["module.py"]
    assert (output / "data.bin").read_bytes() == b"payload"
    assert (output / ".git" / "bulk.bin").read_bytes() == b"unanalysed"
    assert original.read_text() == DUPLICATES
    assert _snapshot(root) == before
