"""Restricting global re-passes to changed files changes cost, not outcome.

The argument is in docs/ARCHITECTURE.md ("Incremental global passes, and
why they are exact"). This test pins the observable claim: the fixed point
over a multi-file project produces byte-identical files with the
restriction on and off, and the restriction really skips pairs.
"""

from __future__ import annotations

import contextlib
import io
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from towel.unification.refactor_engine import UnificationRefactorEngine

EXAMPLES = Path(__file__).parent.parent / "test_examples_crossfile"
PROJECTS = sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir())


def _fixed_point(source: Path, out: Path, incremental: bool) -> tuple[dict[str, bytes], int]:
    engine = UnificationRefactorEngine(
        incremental_global_passes=incremental, cross_module_helpers=True
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        results, _ = engine.refactor_directory_to_fixed_point(
            str(source), str(out), max_iterations=0, progress="none"
        )
    files = {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*.py"))}
    return files, sum(count for count, _ in results.values())


def _standalone(project: str, tmp_path: Path) -> Path:
    """The fixture copied to a directory of its own: in this checkout its expected output
    sits beside it, a second copy of every module, and would make each name ambiguous."""
    source = tmp_path / "project" / project
    shutil.copytree(EXAMPLES / project, source)
    return source


@pytest.mark.parametrize("project", PROJECTS)
def test_restricted_global_passes_give_identical_output(project: str, tmp_path: Path) -> None:
    source = _standalone(project, tmp_path)
    full, applied_full = _fixed_point(source, tmp_path / "full", False)
    restricted, applied_restricted = _fixed_point(source, tmp_path / "restricted", True)
    assert applied_full == applied_restricted
    assert full == restricted


def test_restriction_skips_unchanged_pairs(tmp_path: Path) -> None:
    source = _standalone(PROJECTS[0], tmp_path)
    engine = UnificationRefactorEngine(incremental_global_passes=True)
    seen: list[object] = []
    original = engine.find_block_pairs

    def spy(functions, *, progress="none", changed_files=None):
        seen.append(changed_files)
        return original(functions, progress=progress, changed_files=changed_files)

    with (
        patch.object(engine, "find_block_pairs", spy),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        engine.refactor_directory_to_fixed_point(
            str(source), str(tmp_path / "out"), max_iterations=0, progress="none"
        )
    # The first global pass is unrestricted; a later one, if the project
    # needed one, names the rewritten files.
    assert seen and seen[0] is None
    later = [c for c in seen[1:] if c is not None]
    assert all(isinstance(c, frozenset) and c for c in later)


def _write_two_round_project(source: Path) -> None:
    """Two cross-file duplicates; the second is found only once the first is applied.

    ``alpha`` and ``beta`` share a block across ``a.py`` and ``b.py``;
    ``alpha_two`` and ``gamma`` share another across ``a.py`` and ``c.py``.
    After the first extraction rewrites ``a.py`` and ``b.py``, the restricted
    global pass re-pairs only their functions, and must still pair the
    rewritten ``a.py`` against the untouched ``c.py``.
    """
    source.mkdir()
    (source / "a.py").write_text(
        "def alpha(items):\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += item * 3\n"
        '    print(total, "alpha")\n'
        "    return total\n"
        "\n"
        "\n"
        "def alpha_two(records):\n"
        "    names = []\n"
        "    for record in records:\n"
        "        names.append(record.name.strip().lower())\n"
        '    print(names, "alpha_two")\n'
        "    return names\n"
    )
    # Two top-level modules share a helper only when the borrower already
    # imports the host; b and c import a.
    (source / "b.py").write_text(
        "import a\n"
        "\n"
        "\n"
        "def beta(values):\n"
        "    total = 0\n"
        "    for value in values:\n"
        "        total += value * 3\n"
        '    print(total, "beta")\n'
        "    return total\n"
    )
    (source / "c.py").write_text(
        "import a\n"
        "\n"
        "\n"
        "def gamma(entries):\n"
        "    names = []\n"
        "    for entry in entries:\n"
        "        names.append(entry.name.strip().lower())\n"
        '    print(names, "gamma")\n'
        "    return names\n"
    )


def test_a_restricted_pass_still_pairs_a_rewritten_file_with_an_untouched_one(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    _write_two_round_project(source)
    full, applied_full = _fixed_point(source, tmp_path / "full", False)
    restricted, applied_restricted = _fixed_point(source, tmp_path / "restricted", True)
    assert applied_full == applied_restricted == 4  # two helpers, each rewriting two files
    assert full == restricted
    assert full["c.py"].startswith(b"import a\nfrom a import ")
    assert full["b.py"].startswith(b"import a\nfrom a import ")
