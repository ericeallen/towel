import io
import sys
from pathlib import Path

from towel.unification.refactor_engine import UnificationRefactorEngine


def _make_fixture(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    code = """
def f1():
    x = 1
    y = x + 2
    return y

def f2():
    x = 1
    y = x + 2
    return y

def g1():
    for i in range(3):
        q = i * 2
    return q

def g2():
    for i in range(3):
        q = i * 2
    return q
""".strip()
    (dir_path / "sample.py").write_text(code, encoding="utf-8")


def test_detail_progress_lists_proposals(tmp_path: Path) -> None:
    fixture = tmp_path / "proj"
    _make_fixture(fixture)
    engine = UnificationRefactorEngine(min_lines=3)
    out_dir = tmp_path / "out_dir"  # outside input to avoid recursive copy
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        results, termination = engine.refactor_directory_to_fixed_point(
            str(fixture), str(out_dir), max_iterations=1, progress="detail"
        )
    finally:
        sys.stdout = old_stdout
    output = captured.getvalue()
    assert "Discovered" in output
    assert "Extract common code" in output  # At least one proposal listed
    # iteration_cap expected because we limited iterations to 1 with >1 proposals available
    assert termination == "iteration_cap"
    # We applied exactly one refactoring
    total_applied = sum(c for c, _ in results.values())
    assert total_applied == 1


def test_termination_reason_fixed_point(tmp_path: Path) -> None:
    fixture = tmp_path / "proj_fixed"
    fixture.mkdir(parents=True, exist_ok=True)
    # Only one duplicate group so fixed point after applying it
    code = """
def a():
    x = 1
    y = x + 2
    return y

def b():
    x = 1
    y = x + 2
    return y
""".strip()
    (fixture / "only.py").write_text(code, encoding="utf-8")
    engine = UnificationRefactorEngine(min_lines=3)
    out_dir = tmp_path / "out_dir_fixed"  # outside input
    results, termination = engine.refactor_directory_to_fixed_point(
        str(fixture), str(out_dir), max_iterations=0, progress="none"
    )
    assert termination == "fixed_point"
    total = sum(c for c, _ in results.values())
    assert total == 1
