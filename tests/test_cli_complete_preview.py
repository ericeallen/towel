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

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from towel import cli
from towel.diagnostics import LOG
from towel.filesystem import StagedProject, staged_project
from towel.formatting import FormattingChangedCode
from towel.type_inference import CheckFailure, CheckSuccess, TypeDiagnostic, TypeOracle
from towel.unification import fixed_point
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_checker_failure_reporting import _Oracle
from tests.test_cli_integration import DUPLICATES, invoke
from tests.test_progress_modes import _make_fixture, _two_round_project
from tests.typed_fixtures import requires_mypy


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
    """Observe only the destination, after the real complete pipeline has succeeded."""
    completed: list[tuple[Path, dict[str, bytes]]] = []
    execute = cli._execute_dry

    def capture(options: cli.DryOptions, *, preview: bool = False) -> None:
        execute(options, preview=preview)
        if preview:
            target = Path(options.output)
            completed.append((target, _contents(target)))

    monkeypatch.setattr(cli, "_execute_dry", capture)
    return completed


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
    assert f"Termination: {'iteration_cap' if cap else 'fixed_point'}" in preview.stdout
    assert "Applied " not in preview.stdout
    assert "towel-preview-" not in preview.stdout + preview.stderr
    assert "Call sites (- before / + after):" in preview.stdout
    assert _snapshot(root) == before
    assert len(completed) == 1
    temporary, predicted = completed[0]
    assert not temporary.parent.exists(), "The complete preview removes its private output"
    output = tmp_path / ("output" if directory else "output.py")
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert dry.status == 0, dry
    assert f"Applied {count} refactoring(s)" in dry.stdout
    assert _contents(output) == predicted
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
    ]
    preview = cli.PreviewOptions.from_namespace(parser.parse_args(["preview", "src", *flags]))
    dry = cli.DryOptions.from_namespace(
        parser.parse_args(["dry", "src", "out", "--no-interactive", *flags])
    )
    assert preview.dry_options("out") == dry
    defaults = cli.PreviewOptions.from_namespace(parser.parse_args(["preview", "src"]))
    assert defaults.types and defaults.format and not defaults.quick
    assert defaults.max_refactorings == 0


@pytest.mark.parametrize("directory", [False, True])
def test_preview_shows_final_helper_definitions_and_dry_inventory(
    tmp_path: Path, directory: bool
) -> None:
    """A list of replaced calls alone cannot show the helper's behavior or its final bindings."""
    root = tmp_path / "input"
    root.mkdir()
    (root / "module.py").write_text(DUPLICATES)
    target = root if directory else root / "module.py"
    flags = ["--no-types", "--no-format", "--progress", "none"]
    result = invoke(["preview", str(target), "--interactive", *flags])
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
    assert (root / "module.py").read_text() == DUPLICATES
    compatible = invoke(["preview", str(target), "--no-interactive", *flags])
    assert compatible.stdout == result.stdout and compatible.status == 0


def test_complete_preview_keeps_cross_module_followups_and_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-analysis after rewriting the shared host must still find its other extraction."""
    root = tmp_path / "project"
    _two_round_project(root)
    (root / "pyproject.toml").write_text("[project]\nname = 'preview-fixture'\nversion = '0'\n")
    (root / "vendor").mkdir()
    (root / "vendor" / "ignored.py").write_text("this is intentionally invalid Python!!!\n")
    completed = _capture_preview(monkeypatch)
    flags = [
        "--no-types",
        "--no-format",
        "--cross-module",
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
    assert "Termination: fixed_point" in preview.stdout
    assert "towel-preview-" not in preview.stdout + preview.stderr
    output = tmp_path / "output"
    dry = invoke(["dry", str(root), str(output), "--no-interactive", *flags])
    assert dry.status == 0, dry
    assert _contents(output) == completed[0][1]


def test_quick_is_explicitly_partial_and_does_not_run_checkers_or_formatters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only --quick retains the old single-analysis display, with no applicability promise."""
    source = tmp_path / "module.py"
    source.write_text(DUPLICATES)
    forbidden = Mock(side_effect=AssertionError("quick must only analyze"))
    for name in ("_execute_dry", "_type_oracle", "_generated_code_formatter", "_import_sorter"):
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
    assert "refused by the type checker 1" in preview.stdout
    assert "refused by the type checker 1" in dry.stdout
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
    source = DUPLICATES.replace("(x):", "(x: int) -> int:")
    (package / "module.py").write_text(source)
    config = "[tool.mypy]\nstrict = true\n[tool.black]\nline-length = 88\n"
    (root / "pyproject.toml").write_text(config)
    (root / "consumer.py").write_text("from pkg.module import first\nvalue: int = first(3)\n")
    before = _snapshot(root)
    observed: list[Path] = []

    @contextmanager
    def stage(
        origin_root: Path, origin_target: Path, output: Path, *, limit: int
    ) -> Iterator[StagedProject]:
        assert origin_root == root and origin_target == package
        with staged_project(origin_root, origin_target, output, limit=limit) as staged:
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
    private_outputs: list[Path] = []
    execute = cli._execute_dry

    def capture(options: cli.DryOptions, *, preview: bool = False) -> None:
        if preview:
            private_outputs.append(Path(options.output))
        execute(options, preview=preview)

    def select(_path: Path) -> TypeOracle:
        oracle = Mock(spec=TypeOracle)
        oracle.check_project.return_value = CheckFailure("test checker unavailable")
        closed.append(oracle)
        return oracle

    def fail_final(_engine: UnificationRefactorEngine, paths: Sequence[str]) -> None:
        finals.append(tuple(paths))
        raise RefactoringError(f"test final check refused {paths[0]}")

    monkeypatch.setattr(cli, "_execute_dry", capture)
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
    assert private_outputs and all(not path.parent.exists() for path in private_outputs)
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
    source.write_text(DUPLICATES)
    target = root if directory else source
    selections: list[Path] = []

    def fail(_source: str) -> str:
        raise FormattingChangedCode("test formatter failed")

    def select(path: Path):
        selections.append(path)
        return fail

    monkeypatch.setattr(cli, "_generated_code_formatter", select)
    monkeypatch.setattr(cli, "_import_sorter", lambda path: None)
    flags = ["--no-types", "--progress", "none"]
    preview = invoke(["preview", str(target), *flags])
    output = tmp_path / ("out" if directory else "out.py")
    dry = invoke(["dry", str(target), str(output), "--no-interactive", *flags])
    assert preview.status == dry.status == 0, (preview, dry)
    assert "Would apply 0 refactoring(s)" in preview.stdout
    assert "the formatter changed its code or failed 1" in preview.stdout
    assert "the formatter changed its code or failed 1" in dry.stdout
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
