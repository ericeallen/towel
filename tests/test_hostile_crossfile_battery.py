"""Execute cross-file hostile fixtures before and after directory refactoring.

Each fixture directory holds a package whose modules duplicate a block that
reads a module-level name with a different meaning in each module: a local
function, a class, an import alias, or ``__file__``. The helper must receive
that name from its caller rather than resolve it in the helper's own module.

As in ``test_hostile_battery``, ``TRANSFORMED`` pins which packages the
current engine rewrites, so a lost cross-file extraction fails as loudly as
a wrong one. Every package is in exactly one state: all ten (``xf3`` to
``xf12``) are transformed and none is rejected. A fixture that the engine
must refuse belongs in the docstring's rejected list, not in ``TRANSFORMED``.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shutil
import tempfile

import pytest

from tests.hostile_execution import observe
from towel.unification.refactor_engine import UnificationRefactorEngine

CASES = Path(__file__).parent / "hostile_crossfile"

TRANSFORMED = {
    "xf3_same_named_local_function",
    "xf4_same_named_class",
    "xf5_dunder_file",
    "xf6_same_alias_different_import",
    "xf7_docstring_import",
    "xf8_helper_name_collision",
    "xf9_same_named_base_class",
    "xf10_reuse_existing_function",
    "xf11_package_init_reaches_back",
    "xf12_cycle_through_package_init",
}


def _python_files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*.py"))}


def _run(root: Path) -> tuple[int, str, list[str]]:
    return observe("run.py", root)


@pytest.mark.parametrize("case", sorted(path.name for path in CASES.iterdir() if path.is_dir()))
def test_directory_refactoring_preserves_program_output(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix="towel-hostile-xf-") as directory:
        before = Path(directory) / "before"
        after = Path(directory) / "after"
        shutil.copytree(CASES / case, before)
        shutil.copytree(CASES / case, after)
        engine = UnificationRefactorEngine(min_lines=3)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            results, _ = engine.refactor_directory_to_fixed_point(
                str(after / "pkg"), str(after / "pkg"), progress="none"
            )
        assert results, "Each fixture must exercise a real cross-file extraction"
        transformed = _python_files(after) != _python_files(before)
        assert transformed == (sum(applied for applied, _ in results.values()) > 0)
        assert _run(after) == _run(before)
        assert transformed == (case in TRANSFORMED), (
            "rejected" if not transformed else "transformed"
        )
