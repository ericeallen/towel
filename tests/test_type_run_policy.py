"""Refactoring requires a clean original project unless type checks are explicitly omitted."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
import logging
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from towel.type_inference import (
    CheckFailure,
    CheckResult,
    CheckSuccess,
    MypyInferrer,
    RevealKey,
    RevealRequest,
    Subtyping,
    TypeDiagnostic,
)
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine


def _source(*, typed: bool = True) -> str:
    signature = "value: int) -> int" if typed else "value)"
    return "\n".join(
        f"def {name}({signature}:\n"
        "    total = value + 1\n"
        "    doubled = total * 2\n"
        "    answer = doubled - 3\n"
        "    return answer\n"
        for name in ("first", "second")
    )


class _Oracle:
    """Record policy boundaries independently of any installed checker."""

    def __init__(self, verdict: Callable[[Mapping[str, str]], CheckResult]) -> None:
        self.verdict = verdict
        self.checks: list[dict[str, str]] = []
        self.inferences = 0
        self.closed = False

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        self.checks.append(dict(sources))
        return self.verdict(sources)

    def check(self, file_path: str, source: str) -> CheckResult:
        return self.check_project({file_path: source})

    def reveal(self, requests: Sequence[RevealRequest]) -> Mapping[RevealKey, str]:
        assert self.checks, "Inference ran before the original project check"
        self.inferences += 1
        return {}

    def is_subtype(
        self, file_path: str, source: str, pairs: Sequence[tuple[str, str]]
    ) -> Sequence[Subtyping]:
        assert self.checks, "Subtyping ran before the original project check"
        self.inferences += 1
        return [Subtyping.UNKNOWN for _ in pairs]

    def close(self) -> None:
        self.closed = True


def _helper_signature(source: str) -> str:
    helper = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and "extracted_func" in node.name
    )
    return ast.unparse(helper).splitlines()[0]


def test_dirty_direct_run_refuses_before_inference_and_keeps_the_original(tmp_path: Path) -> None:
    path, consumer = tmp_path / "program.py", tmp_path / "consumer.py"
    original = _source()
    path.write_text(original)
    consumer.write_text('broken: int = "wrong"\n')
    oracle = _Oracle(lambda _: CheckSuccess((TypeDiagnostic(str(consumer), "Existing error"),)))
    engine = UnificationRefactorEngine(reuse_existing_functions=False, type_oracle=oracle)
    proposal = engine.analyze_files([str(path), str(consumer)])[0]
    for _ in range(2):
        with pytest.raises(
            RefactoringError, match=r"(?s)Original project.*1 type error.*--no-types"
        ):
            engine.apply_refactoring(str(path), proposal)
    assert oracle.checks == [{str(path): original, str(consumer): consumer.read_text()}]
    assert oracle.inferences == 0
    assert engine.type_oracle is oracle and not oracle.closed
    assert path.read_text() == original


def test_dirty_baseline_reports_bounded_details_and_logs_all_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "program.py"
    path.write_text(_source())
    errors = tuple(TypeDiagnostic(str(path), f"baseline diagnostic {index}") for index in range(5))
    oracle = _Oracle(lambda _: CheckSuccess(errors))
    engine = UnificationRefactorEngine(type_oracle=oracle)
    caplog.set_level(logging.DEBUG, logger="towel.types")
    with pytest.raises(RefactoringError) as raised:
        engine.begin_refactoring_run([str(path)])
    message = str(raised.value)
    assert "reported 5 type error(s)" in message
    assert f"{path}: baseline diagnostic 0" in message
    assert "baseline diagnostic 2" in message and "baseline diagnostic 3" not in message
    assert "and 2 more" in message and "TOWEL_DEBUG_TYPES=1" in message
    assert all(error.message in caplog.text for error in errors)


@pytest.mark.parametrize("mode", ["direct", "file", "directory", "file_copy", "directory_copy"])
@pytest.mark.parametrize("reason", ["checker timed out", "checker worker crashed"])
def test_baseline_failure_refuses_before_inference_or_materialization(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: str, reason: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = project / "program.py"
    output = tmp_path / "output"
    original = _source()
    path.write_text(original)
    oracle = _Oracle(lambda _: CheckFailure(reason))
    engine = UnificationRefactorEngine(type_oracle=oracle, reuse_existing_functions=False)
    proposal = engine.analyze_file(str(path))[0]
    with patch.object(engine, "_materialize_once", wraps=engine._materialize_once) as materialize:
        with pytest.raises(RefactoringError, match=reason):
            if mode == "direct":
                engine.apply_refactoring(str(path), proposal)
            elif mode in {"file", "file_copy"}:
                engine.refactor_to_fixed_point(
                    str(path),
                    progress="none",
                    output_path=str(output) if mode == "file_copy" else None,
                )
            else:
                engine.refactor_directory_to_fixed_point(
                    str(project),
                    str(output if mode == "directory_copy" else project),
                    progress="none",
                )
    assert materialize.call_count == 0
    assert len(oracle.checks) == 1 and oracle.inferences == 0
    assert "disabling" not in caplog.text
    assert path.read_text() == original and engine.change_log == ()
    assert not output.exists()
    assert engine.type_oracle is oracle and not oracle.closed


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_clean_unannotated_run_never_disables_for_prospective_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, reuse: bool, failure: bool
) -> None:
    path = tmp_path / "program.py"
    original = _source(typed=False)
    path.write_text(original)

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        if sources[str(path)] == original:
            return CheckSuccess()
        return (
            CheckFailure("prospective checker timed out")
            if failure
            else CheckSuccess((TypeDiagnostic(str(path), "New error"),))
        )

    oracle = _Oracle(verdict)
    engine = UnificationRefactorEngine(
        type_oracle=oracle, reuse_existing_functions=reuse, annotate_helpers=False
    )
    proposal = engine.analyze_file(str(path))[0]
    assert (proposal.reused_function is not None) == reuse
    for _ in range(2):
        with pytest.raises(RefactoringError, match="[Tt]ype (errors|check failed)"):
            engine.apply_refactoring(str(path), proposal)
    assert len(oracle.checks) == 3, "One baseline and both prospective changes must be checked"
    assert "disabling" not in caplog.text
    assert oracle.inferences == 0
    assert path.read_text() == original and engine.change_log == ()


def test_explicit_new_run_rechecks_but_local_analysis_does_not(tmp_path: Path) -> None:
    path = tmp_path / "program.py"
    path.write_text(_source())
    dirty = True

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        return (
            CheckSuccess((TypeDiagnostic(str(path), "Existing error"),))
            if dirty
            else CheckSuccess()
        )

    oracle = _Oracle(verdict)
    engine = UnificationRefactorEngine(type_oracle=oracle, reuse_existing_functions=False)
    proposal = engine.analyze_file(str(path))[0]
    with pytest.raises(RefactoringError, match=r"(?s)Original project.*--no-types"):
        engine.apply_refactoring(str(path), proposal)
    dirty = False
    engine.analyze_file(str(path))
    with pytest.raises(RefactoringError, match=r"(?s)Original project.*--no-types"):
        engine.apply_refactoring(str(path), proposal)
    assert len(oracle.checks) == 1 and oracle.inferences == 0
    engine.begin_refactoring_run([str(path)])
    engine.apply_refactoring(str(path), proposal)
    assert len(oracle.checks) == 3 and oracle.inferences > 0


def test_directory_policy_survives_multiple_applied_proposals(tmp_path: Path) -> None:
    # Independent modules with differently shaped computations require two
    # applications, including the driver's localized reanalysis between them.
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    first.write_text(_source())
    second.write_text(_source().replace("total = value + 1", "total = abs(value)"))
    original = {str(path): path.read_text() for path in (first, second)}
    oracle = _Oracle(lambda _: CheckSuccess())
    engine = UnificationRefactorEngine(type_oracle=oracle)
    results, _ = engine.refactor_directory_to_fixed_point(
        str(tmp_path), str(tmp_path), max_iterations=2, progress="none"
    )
    assert sum(count for count, _ in results.values()) == 2
    assert len(oracle.checks) == 3 and oracle.inferences == 0
    assert oracle.checks[0] == original, "One original baseline precedes both applications"


def test_fixed_point_calls_start_fresh_runs_on_the_same_engine(tmp_path: Path) -> None:
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    for path in (first, second):
        path.write_text(_source())
    dirty = True

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        return (
            CheckSuccess((TypeDiagnostic(str(first), "Existing error"),))
            if dirty
            else CheckSuccess()
        )

    oracle = _Oracle(verdict)
    engine = UnificationRefactorEngine(type_oracle=oracle)
    with pytest.raises(RefactoringError, match=r"(?s)Original project.*--no-types"):
        engine.refactor_to_fixed_point(str(first), max_iterations=1, progress="none")
    assert first.read_text() == _source()
    assert len(oracle.checks) == 1
    dirty = False
    assert engine.refactor_to_fixed_point(str(second), max_iterations=1, progress="none")[1] == 1
    assert len(oracle.checks) == 3, "The second run must check its baseline and prospective project"


@pytest.mark.parametrize("dirty", [False, True])
def test_real_complete_baseline_includes_unchanged_consumer(tmp_path: Path, dirty: bool) -> None:
    pytest.importorskip("mypy")
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = true\nfiles = program.py, consumer.py\n")
    path, consumer = tmp_path / "program.py", tmp_path / "consumer.py"
    path.write_text(_source())
    consumer.write_text('broken: int = "wrong"\n' if dirty else "valid: int = 1\n")
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle, reuse_existing_functions=False)
        proposal = engine.analyze_file(str(path))[0]
        with (
            patch.object(oracle, "check_project", wraps=oracle.check_project) as check,
            patch.object(oracle, "reveal", wraps=oracle.reveal) as reveal,
        ):
            if dirty:
                with pytest.raises(RefactoringError, match=r"(?s)Original project.*--no-types"):
                    engine.apply_refactoring(str(path), proposal)
            else:
                assert "__extracted_func_0" in engine.apply_refactoring(str(path), proposal)
        assert check.call_count == (1 if dirty else 2)
        assert bool(reveal.call_count) is not dirty
        assert consumer.read_text() == ('broken: int = "wrong"\n' if dirty else "valid: int = 1\n")
    finally:
        oracle.close()


def test_later_project_errors_cannot_become_original_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("mypy")
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = true\nfiles = program.py, consumer.py\n")
    path, consumer = tmp_path / "program.py", tmp_path / "consumer.py"
    path.write_text(_source())
    consumer.write_text("valid: int = 1\n")
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle)
        engine.begin_refactoring_run([str(path)])
        consumer.write_text('valid: int = "now invalid"\n')
        proposal = engine.analyze_file(str(path))[0]
        with pytest.raises(RefactoringError, match="Reusing.*type errors"):
            engine.apply_refactoring(str(path), proposal)
        assert "disabling" not in caplog.text
        assert path.read_text() == _source() and engine.change_log == ()
    finally:
        oracle.close()


@pytest.mark.parametrize("dirty", [False, True])
def test_library_copied_directory_keeps_original_consumers_and_oracle_ownership(
    tmp_path: Path, dirty: bool
) -> None:
    pytest.importorskip("mypy")
    project = tmp_path / "project"
    project.mkdir()
    (project / "mypy.ini").write_text("[mypy]\nstrict = true\nfiles = src, consumer.py\n")
    source = project / "src"
    source.mkdir()
    (source / "__init__.py").write_text("")
    path = source / "program.py"
    path.write_text(_source())
    (project / "consumer.py").write_text(
        'broken: int = "wrong"\n'
        if dirty
        else "from src.program import first\nvalue: int = first(1)\n"
    )
    output = tmp_path / "refactored"
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle)
        with patch.object(oracle, "check_project", wraps=oracle.check_project) as check:
            if dirty:
                with pytest.raises(RefactoringError, match=r"(?s)Original project.*--no-types"):
                    engine.refactor_directory_to_fixed_point(
                        str(source), str(output), max_iterations=1, progress="none"
                    )
                assert not output.exists(), "A rejected run must not reserve the output path"
            else:
                results, _ = engine.refactor_directory_to_fixed_point(
                    str(source), str(output), max_iterations=1, progress="none"
                )
                assert sum(count for count, _ in results.values()) == 1
                assert "return first(value)" in (output / "program.py").read_text()
                # The checker reads the run's stage under the original's names,
                # and the stage itself is excluded from the project it checks.
                excluded = check.call_args_list[1].kwargs["excluded_paths"]
                assert any("towel-stage-" in path for path in excluded)
                assert not any(Path(path).exists() for path in excluded)
        assert check.call_count == (1 if dirty else 2)
        assert str(path) in check.call_args_list[0].args[0]
        assert path.read_text() == _source()
        assert engine.type_oracle is oracle
        assert not isinstance(oracle.check(str(path), path.read_text()), CheckFailure)
    finally:
        oracle.close()


@pytest.mark.parametrize("directory", [False, True])
def test_cli_refuses_before_copy_and_explicit_no_types_allows_the_same_output(
    tmp_path: Path, directory: bool
) -> None:
    pytest.importorskip("mypy")
    project = tmp_path / "project"
    project.mkdir()
    (project / "mypy.ini").write_text("[mypy]\nstrict = true\nfiles = program.py, consumer.py\n")
    source = project / "program.py"
    original = "total = value + 2".join(_source().rsplit("total = value + 1", 1))
    source.write_text(original)
    (project / "consumer.py").write_text('broken: int = "wrong"\n')
    output = tmp_path / ("refactored" if directory else "refactored.py")
    command = [
        sys.executable,
        "-c",
        "from towel.cli import main; main()",
        "dry",
        str(project if directory else source),
        str(output),
        "--no-format",
        "--no-interactive",
        "--progress",
        "none",
    ]
    run = subprocess.run(
        command,
        cwd=project,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert run.returncode != 0, run.stdout + run.stderr
    assert "Original project check reported" in run.stderr and "--no-types" in run.stderr
    assert not output.exists(), "The suggested rerun must be able to use the same destination"
    bypass = subprocess.run(
        [*command, "--no-types"],
        cwd=project,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert bypass.returncode == 0, bypass.stdout + bypass.stderr
    result = (output / "program.py" if directory else output).read_text()
    signature = _helper_signature(result)
    assert ": int" not in signature and " -> " not in signature
    assert "def first(value: int) -> int:" in result
    assert "def second(value: int) -> int:" in result
    assert source.read_text() == original
