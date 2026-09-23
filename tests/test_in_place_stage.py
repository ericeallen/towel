"""An in-place run refactors a private stage and writes the project once, when it has succeeded.

The run used to rewrite the project one refactoring at a time, so when the
cold confirmation at the end refused the result -- a checker that could not
run, or one that found what the warm checks missed -- the refactorings stayed
written and the command exited 1, telling the user to check the project
themselves. It now takes the out-of-place path: the whole project is staged,
the target is refactored and confirmed there, and only then is the combined
change applied to the project as one journaled plan. A failure anywhere
before that leaves the project exactly as it was.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
from typing import Dict, Iterator, List, Mapping, Sequence

import pytest

from towel import changes
from towel.type_inference import CheckResult, CheckSuccess, MypyInferrer, TypeDiagnostic
from towel.unification import fixed_point
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine
from tests.test_cli_integration import invoke

pytest.importorskip("mypy")

BODY = "    total = value + 1\n    doubled = total * 2\n    answer = doubled - {offset}\n"
MODULE = "".join(
    f"def {name}(value: int) -> int:\n    print({name!r})\n"
    + BODY.format(offset=offset)
    + "    return answer\n\n\n"
    for name, offset in (("first", 3), ("second", 4))
)


@pytest.fixture
def private_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temporary directory of the test's own, so a leftover stage can be seen."""
    directory = tmp_path / "temp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    yield directory


def _project(root: Path) -> Dict[str, Path]:
    package = root / "pkg"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\nversion = "0"\n\n[tool.mypy]\nstrict = true\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "a.py").write_text(MODULE, encoding="utf-8")
    (package / "b.py").write_text(MODULE.replace("first", "third"), encoding="utf-8")
    (package / "data.bin").write_bytes(b"\0" * 64)
    return {path.name: path for path in package.iterdir()}


def _snapshot(root: Path) -> Dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


class _BlindWhileWarm(MypyInferrer):
    """mypy that waves every candidate through while warm and objects once started cold."""

    def __init__(self) -> None:
        super().__init__()
        self.cold = False

    def forget_warm_state(self) -> None:
        super().forget_warm_state()
        self.cold = True

    def check_project(
        self, sources: Mapping[str, str], *, excluded_paths: Sequence[str] = ()
    ) -> CheckResult:
        real = super().check_project(sources, excluded_paths=excluded_paths)
        if not self.cold:
            return real
        invented = TypeDiagnostic(next(iter(sources)), "mypy: invented: missed while warm", 1)
        return CheckSuccess((*getattr(real, "errors", ()), invented))


@pytest.mark.parametrize("single_file", [False, True])
def test_a_refused_confirmation_leaves_the_project_exactly_as_it_was(
    tmp_path: Path, private_temp: Path, single_file: bool
) -> None:
    root = tmp_path / "project"
    files = _project(root)
    before = _snapshot(root)
    oracle = _BlindWhileWarm()
    try:
        engine = UnificationRefactorEngine(
            min_lines=3, reuse_existing_functions=False, type_oracle=oracle
        )
        with pytest.raises(RefactoringError, match="Nothing was written"):
            with contextlib.redirect_stdout(io.StringIO()):
                if single_file:
                    engine.refactor_to_fixed_point(str(files["a.py"]), progress="none")
                else:
                    engine.refactor_directory_to_fixed_point(
                        str(root / "pkg"), str(root / "pkg"), progress="none"
                    )
        assert oracle.cold, "the run reached its cold confirmation"
    finally:
        oracle.close()
    assert _snapshot(root) == before
    assert list(private_temp.iterdir()) == [], "the stage is removed"


def test_the_command_line_says_nothing_was_written(
    tmp_path: Path, private_temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _project(root)
    before = _snapshot(root)
    oracle = _BlindWhileWarm()
    monkeypatch.setattr("towel.cli._type_oracle", lambda path: oracle)
    target = str(root / "pkg")
    result = invoke(
        ["dry", target, target, "--no-interactive", "--no-format", "--progress", "none"]
    )
    assert result.status == 1, result
    assert "Nothing was written" in result.stderr
    assert "listed above" not in result.stderr
    assert _snapshot(root) == before


def test_a_successful_run_writes_the_project_in_one_journaled_plan(
    tmp_path: Path, private_temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _project(root)
    published: List[changes.ChangePlan] = []
    original_apply = changes.apply_changes

    def recording_apply(plan: changes.ChangePlan) -> None:
        if any(change.path.is_relative_to(root.resolve()) for change in plan.changes):
            published.append(plan)
        original_apply(plan)

    monkeypatch.setattr(fixed_point, "apply_changes", recording_apply)
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(root / "pkg"), str(root / "pkg"), progress="none"
        )
    assert sum(count for count, _ in results.values()) >= 2
    assert len(published) == 1, "one plan, applied once, for the whole run"
    assert {change.path.name for change in published[0].changes} == {"a.py", "b.py"}
    assert all(Path(path).is_relative_to(root) for path in results), results
    assert "__extracted_func_0" in (root / "pkg" / "a.py").read_text(encoding="utf-8")
    assert not list(root.rglob(".towel-transaction-*"))
    assert list(private_temp.iterdir()) == []


def test_an_edit_made_during_the_run_stops_the_write_and_survives(
    tmp_path: Path, private_temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stage holds the project as it was; a file edited since cannot take the result."""
    root = tmp_path / "project"
    files = _project(root)
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    original = engine.analyze_directory
    edited = "# edited while Towel ran\n" + MODULE
    edits: List[int] = []

    def edit_during_the_run(*args: object, **kwargs: object) -> object:
        if not edits:
            edits.append(1)
            files["a.py"].write_text(edited, encoding="utf-8")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine, "analyze_directory", edit_during_the_run)
    with pytest.raises(changes.StaleSource, match="changed while"):
        with contextlib.redirect_stdout(io.StringIO()):
            engine.refactor_directory_to_fixed_point(
                str(root / "pkg"), str(root / "pkg"), progress="none"
            )
    assert files["a.py"].read_text(encoding="utf-8") == edited
    assert files["b.py"].read_text(encoding="utf-8") == MODULE.replace("first", "third")
    assert list(private_temp.iterdir()) == []


def test_an_interrupted_run_leaves_the_project_and_no_stage(
    tmp_path: Path, private_temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _project(root)
    before = _snapshot(root)
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    applied: List[int] = []
    original = engine._apply_and_refresh

    def interrupt_after_the_first(*args: object, **kwargs: object) -> object:
        if applied:
            raise KeyboardInterrupt
        applied.append(1)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine, "_apply_and_refresh", interrupt_after_the_first)
    with pytest.raises(KeyboardInterrupt):
        with contextlib.redirect_stdout(io.StringIO()):
            engine.refactor_directory_to_fixed_point(
                str(root / "pkg"), str(root / "pkg"), progress="none"
            )
    assert applied, "one refactoring was applied, to the stage, before the interruption"
    assert _snapshot(root) == before
    assert list(private_temp.iterdir()) == []
