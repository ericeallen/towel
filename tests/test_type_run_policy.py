"""A typed run starts from a check of the original project, and never switches it off.

A checker that cannot run refuses the run before anything is inferred or
written, and --no-types is the way on. Errors the check reports are not a
refusal: they are what every later check is compared with
(tests/test_differential_baseline.py), so errors a change introduces can never
become original errors that disable checking.
"""

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
from tests.probe_answers import answer_probes, only_probes


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
        if not only_probes(requests):
            self.inferences += 1
        return answer_probes(requests)

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


def test_a_direct_run_over_original_errors_checks_once_then_infers(tmp_path: Path) -> None:
    path, consumer = tmp_path / "program.py", tmp_path / "consumer.py"
    original = _source()
    path.write_text(original)
    consumer.write_text('broken: int = "wrong"\n')
    oracle = _Oracle(lambda _: CheckSuccess((TypeDiagnostic(str(consumer), "Existing error"),)))
    engine = UnificationRefactorEngine(reuse_existing_functions=False, type_oracle=oracle)
    proposal = engine.analyze_files([str(path), str(consumer)])[0]
    assert "__extracted_func_0" in engine.apply_refactoring(str(path), proposal)
    assert oracle.checks[0] == {str(path): original, str(consumer): consumer.read_text()}
    assert oracle.inferences > 0, "the baseline's errors do not switch inference off"
    assert engine.type_oracle is oracle and not oracle.closed
    assert path.read_text() == original


def test_original_errors_are_reported_bounded_and_logged_in_full(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "program.py"
    path.write_text(_source())
    errors = tuple(TypeDiagnostic(str(path), f"baseline diagnostic {index}") for index in range(5))
    oracle = _Oracle(lambda _: CheckSuccess(errors))
    engine = UnificationRefactorEngine(type_oracle=oracle)
    caplog.set_level(logging.DEBUG, logger="towel.types")
    engine.begin_refactoring_run([str(path)])
    assert "reports 5 error(s) in 1 file(s)" in caplog.text
    assert "  program.py: 5" in caplog.text and "TOWEL_DEBUG_TYPES=1" in caplog.text
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
    # No function is redirected to another, whatever the setting says.
    assert proposal.reused_function is None
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
    broken = True

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        return CheckFailure("checker timed out") if broken else CheckSuccess()

    oracle = _Oracle(verdict)
    engine = UnificationRefactorEngine(type_oracle=oracle, reuse_existing_functions=False)
    proposal = engine.analyze_file(str(path))[0]
    with pytest.raises(RefactoringError, match=r"(?s)Original project.*--no-types"):
        engine.apply_refactoring(str(path), proposal)
    broken = False
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
    assert len(oracle.checks) == 3
    assert oracle.checks[0] == original, "One original baseline precedes both applications"


def test_fixed_point_calls_start_fresh_runs_on_the_same_engine(tmp_path: Path) -> None:
    first, second = tmp_path / "first.py", tmp_path / "second.py"
    for path in (first, second):
        path.write_text(_source())
    broken = True

    def verdict(sources: Mapping[str, str]) -> CheckResult:
        return CheckFailure("checker worker crashed") if broken else CheckSuccess()

    oracle = _Oracle(verdict)
    engine = UnificationRefactorEngine(type_oracle=oracle)
    with pytest.raises(RefactoringError, match=r"(?s)Original project.*--no-types"):
        engine.refactor_to_fixed_point(str(first), max_iterations=1, progress="none")
    assert first.read_text() == _source()
    assert len(oracle.checks) == 1
    broken = False
    assert engine.refactor_to_fixed_point(str(second), max_iterations=1, progress="none")[1] == 1
    assert len(oracle.checks) == 3, "The second run must check its baseline and prospective project"


@pytest.mark.parametrize("dirty", [False, True])
def test_real_complete_baseline_includes_unchanged_consumer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, dirty: bool
) -> None:
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
            assert "__extracted_func_0" in engine.apply_refactoring(str(path), proposal)
        assert check.call_count == 2 and reveal.call_count
        # Only the unchanged consumer holds an error, so only a baseline that
        # checked it can have reported one.
        assert ("reports 1 error(s) in 1 file(s)" in caplog.text) is dirty
        assert ("consumer.py: 1" in caplog.text) is dirty
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
        with pytest.raises(RefactoringError, match="type errors"):
            engine.apply_refactoring(str(path), proposal)
        assert "disabling" not in caplog.text
        assert path.read_text() == _source() and engine.change_log == ()
    finally:
        oracle.close()


@pytest.mark.parametrize("dirty", [False, True])
def test_library_copied_directory_keeps_original_consumers_and_oracle_ownership(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, dirty: bool
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
            results, _ = engine.refactor_directory_to_fixed_point(
                str(source), str(output), max_iterations=1, progress="none"
            )
            assert sum(count for count, _ in results.values()) == 1
            assert "return __extracted_func_0(value)" in (output / "program.py").read_text()
            # The checker reads the run's stage under the original's names,
            # and the stage itself is excluded from the project it checks.
            excluded = check.call_args_list[1].kwargs["excluded_paths"]
            assert any("towel-stage-" in path for path in excluded)
            assert not any(Path(path).exists() for path in excluded)
        # Baseline, the candidate, and the cold confirmation of the finished run.
        assert check.call_count == 3
        confirmed = check.call_args_list[2].kwargs["excluded_paths"]
        assert any("towel-stage-" in path for path in confirmed)
        assert str(path) in check.call_args_list[0].args[0]
        # The consumer's own error is the original's, left as it was.
        assert ("consumer.py: 1" in caplog.text) is dirty
        assert path.read_text() == _source()
        assert engine.type_oracle is oracle
        assert not isinstance(oracle.check(str(path), path.read_text()), CheckFailure)
    finally:
        oracle.close()


@pytest.mark.parametrize("directory", [False, True])
def test_cli_refactors_over_original_errors_with_types_and_no_types_leaves_helpers_bare(
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
    command = [
        sys.executable,
        "-c",
        "from towel.cli import main; main()",
        "dry",
        str(project if directory else source),
        "--no-format",
        "--no-interactive",
        "--progress",
        "none",
    ]
    typed_output = tmp_path / ("typed" if directory else "typed.py")
    run = subprocess.run(
        [*command[:5], str(typed_output), *command[5:]],
        cwd=project,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "The original project's type check reports 1 error(s) in 1 file(s)." in run.stderr
    assert "consumer.py: 1" in run.stderr
    typed = (typed_output / "program.py" if directory else typed_output).read_text()
    typed_signature = _helper_signature(typed)
    assert ": int" in typed_signature and typed_signature.endswith(" -> int:"), typed_signature
    output = tmp_path / ("refactored" if directory else "refactored.py")
    bypass = subprocess.run(
        [*command[:5], str(output), *command[5:], "--no-types"],
        cwd=project,
        env={**os.environ, "TOWEL_WORKERS": "1"},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert bypass.returncode == 0, bypass.stdout + bypass.stderr
    assert "original project's type check" not in bypass.stderr
    result = (output / "program.py" if directory else output).read_text()
    signature = _helper_signature(result)
    assert ": int" not in signature and " -> " not in signature
    assert "def first(value: int) -> int:" in result
    assert "def second(value: int) -> int:" in result
    assert source.read_text() == original
