"""A project whose layout Towel cannot model loses its cross-file pairs, nothing else."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys

import pytest

from towel.cli import main
from towel.project_layout import ProjectLayout
from towel.unification.exceptions import UnsupportedLayoutError
from towel.unification.refactor_engine import UnificationRefactorEngine

# ``compute`` is defined first: a call site resolves only names bound before
# its function, so a helper defined later would be passed as a thunk and the
# whole-body reuse of ``f1`` this test expects would not apply.
SAME_FILE = (
    "def compute(v):\n    return v\n\n\n"
    "def f1(a):\n    x = a + 1\n    y = x * 2\n    z = y + compute(a)\n    return z\n\n\n"
    "def f2(b):\n    x = b + 1\n    y = x * 2\n    z = y + compute(b)\n    return z\n\n\n"
)
CROSS_A = (
    "def g(items):\n    names = []\n    for item in items:\n"
    "        names.append(item.name.strip().lower())\n    print(names, 'g')\n    return names\n"
)
CROSS_B = (
    "def h(entries):\n    names = []\n    for entry in entries:\n"
    "        names.append(entry.name.strip().lower())\n    print(names, 'h')\n    return names\n"
)


def _hatch_project(root: Path) -> None:
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        '[project]\nname = "proj"\nversion = "0.1"\n'
    )
    (root / "hatch.toml").write_text('[build.targets.wheel]\npackages = ["pkg"]\n')
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "a.py").write_text(SAME_FILE + CROSS_A)
    (root / "pkg" / "b.py").write_text(CROSS_B)


def test_discovery_refuses_the_layout(tmp_path: Path) -> None:
    _hatch_project(tmp_path / "proj")
    with pytest.raises(UnsupportedLayoutError):
        ProjectLayout.discover(tmp_path / "proj" / "pkg" / "a.py")


def test_same_file_extractions_still_happen(tmp_path: Path) -> None:
    _hatch_project(tmp_path / "proj")
    engine = UnificationRefactorEngine(min_lines=3, reuse_existing_functions=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, reason = engine.refactor_directory_to_fixed_point(
            str(tmp_path / "proj"), str(tmp_path / "out"), max_iterations=0, progress="none"
        )
    assert reason == "fixed_point"
    changed = {Path(path).name: count for path, (count, _) in results.items()}
    assert changed == {"a.py": 1}
    assert (tmp_path / "out" / "pkg" / "b.py").read_text() == CROSS_B


def test_the_command_line_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hatch_project(tmp_path / "proj")
    argv = [
        "towel",
        "dry",
        str(tmp_path / "proj"),
        str(tmp_path / "out"),
        "--no-types",
        "--no-format",
        "--no-interactive",
        "--progress",
        "none",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        main()
    rewritten = (tmp_path / "out" / "pkg" / "a.py").read_text()
    assert "def f2(b):\n    return f1(b)\n" in rewritten  # the same-file pair, by reuse
    assert (tmp_path / "out" / "pkg" / "b.py").read_text() == CROSS_B
