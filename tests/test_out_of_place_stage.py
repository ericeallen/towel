"""The private stage an out-of-place run refactors: what it holds, and that it never outlives the run.

A failed run must leave nothing at the output path, or the retry is refused
with "Output already exists"; and the stage, a copy of the whole project,
must be removed however the run ends.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Iterator, List

import pytest

from towel.filesystem import ProjectTooLarge, staged_project
from towel.unification import fixed_point
from towel.unification.refactor_engine import UnificationRefactorEngine

BODY = "    total = 0\n    for item in items:\n        total += item * {factor}\n    return total\n"
MODULE = "def a1(items):\n" + BODY.format(factor=2) + "\n\ndef a2(items):\n" + BODY.format(factor=3)


@pytest.fixture
def private_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temporary directory of the test's own, so a leftover stage can be seen."""
    directory = tmp_path / "temp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    yield directory


def _project(root: Path) -> Path:
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0"\n')
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(MODULE)
    (package / "data.txt").write_text("kept")
    (root / "other").mkdir()
    (root / "other" / "__init__.py").write_text("")
    (root / "other" / "c.py").write_text("C = 1\n")
    (root / "notes.txt").write_text("not an input")
    (root / ".git").mkdir()
    (root / ".git" / "hook.py").write_text("")
    return package


def _leftovers(directory: Path) -> List[Path]:
    return sorted(directory.iterdir())


def test_the_stage_holds_the_whole_project_and_the_target_whole(
    tmp_path: Path, private_temp: Path
) -> None:
    project = tmp_path / "project"
    package = _project(project)
    with staged_project(project, package, tmp_path / "out", limit=100) as stage:
        assert stage.target == stage.root / "pkg"
        assert (stage.root / "pyproject.toml").is_file()
        assert (stage.root / "other" / "c.py").read_text() == "C = 1\n"
        assert (stage.target / "data.txt").read_text() == "kept"
        assert not (stage.root / "notes.txt").exists()
        assert not (stage.root / ".git").exists()
        assert stage.public(str(stage.target / "a.py")) == str(tmp_path / "out" / "a.py")
        assert stage.public(str(stage.root / "other" / "c.py")) == str(project / "other" / "c.py")
    assert _leftovers(private_temp) == []


def test_a_project_too_large_to_stage_is_refused_before_anything_is_written(
    tmp_path: Path, private_temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    package = _project(project)
    monkeypatch.setattr(fixed_point, "MAXIMUM_FILES", 2)
    output = tmp_path / "out"
    with pytest.raises(ProjectTooLarge, match="Copy the project yourself"):
        UnificationRefactorEngine(min_lines=3).refactor_directory_to_fixed_point(
            str(package), str(output), progress="none"
        )
    assert not output.exists()
    assert _leftovers(private_temp) == []


@pytest.mark.parametrize("single_file", [False, True])
def test_a_failed_run_leaves_neither_output_nor_stage(
    tmp_path: Path, private_temp: Path, monkeypatch: pytest.MonkeyPatch, single_file: bool
) -> None:
    project = tmp_path / "project"
    package = _project(project)

    def interrupted(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    engine = UnificationRefactorEngine(min_lines=3)
    monkeypatch.setattr(engine, "analyze_files", interrupted)
    monkeypatch.setattr(engine, "analyze_directory", interrupted)
    output = tmp_path / ("out.py" if single_file else "out")
    with pytest.raises(KeyboardInterrupt):
        if single_file:
            engine.refactor_to_fixed_point(
                str(package / "a.py"), progress="none", output_path=str(output)
            )
        else:
            engine.refactor_directory_to_fixed_point(str(package), str(output), progress="none")
    assert not output.exists(), "a retry would be refused with 'Output already exists'"
    assert _leftovers(private_temp) == []
    assert (package / "a.py").read_text() == MODULE


def test_a_single_file_is_refactored_in_its_project_and_published_alone(
    tmp_path: Path, private_temp: Path
) -> None:
    project = tmp_path / "project"
    package = _project(project)
    output = tmp_path / "out.py"
    engine = UnificationRefactorEngine(min_lines=3)
    _code, applied, _ = engine.refactor_to_fixed_point(
        str(package / "a.py"), progress="none", output_path=str(output)
    )
    assert applied == 1
    assert "__extracted_func_0" in output.read_text()
    assert (package / "a.py").read_text() == MODULE
    assert {change.path for change in engine.change_log} == {str(output)}
    assert _leftovers(private_temp) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.py", "project", "temp"]
