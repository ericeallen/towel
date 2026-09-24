"""Execute cross-file hostile fixtures before and after directory refactoring.

Each fixture directory holds a package whose modules duplicate a block that
reads a module-level name with a different meaning in each module: a local
function, a class, an import alias, ``__file__``, or a builtin that only one
module rebinds (by definition, star import, a local of its function, or its
``__builtins__``), including one only the ancestor class's module rebinds.
A module name must reach the helper from its caller rather than resolve in
the helper's own module. A builtin cannot: no helper takes one as a
parameter, so a pair whose modules may disagree about it is declined.

As in ``test_hostile_battery``, ``TRANSFORMED`` pins which packages the
current engine rewrites, so a lost cross-file extraction fails as loudly as
a wrong one. Every package is in exactly one state. Rejected today:
``xf13_import_time_effects``, whose helper import would run a module that
prints at import time, which the borrower's own import never ran; the four
whose borrower rebinds ``len`` (``xf17``, ``xf18``, ``xf19``, ``xf22``); and
``xf23_relative_import_in_another_package``, whose subpackages ``pkg.x`` and
``pkg.y`` never import each other, so neither may gain an import of the
other (docs/DECISIONS.md, "Import names come from the program").
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shutil
import tempfile

import pytest

from tests.hostile_execution import module_faces, observe
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
    "xf14_script_with_leading_statement",
    "xf15_ancestor_in_another_module",
    "xf16_consumer_outside_target_owns_helper_name",
    "xf20_builtin_shadowed_in_ancestor_module",
    "xf21_builtin_shadowed_in_third_module",
    "xf24_relative_import_climbs_elsewhere",
    "xf25_relative_import_in_the_same_package",
    "xf26_relative_import_ancestor_in_another_package",
    "xf27_registration_decorator_in_host",
    "xf28_registration_decorator_in_reused_module",
    "xf29_type_checking_block_with_branches",
}

# Packages the engine must leave alone, with the reason a comment in the fixture.
REJECTED = {
    "xf13_import_time_effects",
    "xf17_builtin_shadowed_in_borrower",
    "xf18_builtin_shadowed_by_star_import",
    "xf19_builtin_shadowed_by_borrower_local",
    "xf22_borrower_rebinds_builtins_namespace",
    "xf23_relative_import_in_another_package",
}


def _python_files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*.py"))}


def _run(root: Path) -> tuple[int, str, list[str]]:
    return observe("run.py", root)


def _modules(root: Path) -> list[str]:
    """Every module of the fixture but its script, ``run.py``, by the name it is imported under."""
    names = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).with_suffix("").parts
        if parts == ("run",):
            continue
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


@pytest.mark.parametrize("case", sorted(path.name for path in CASES.iterdir() if path.is_dir()))
def test_directory_refactoring_preserves_program_output(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix="towel-hostile-xf-") as directory:
        before = Path(directory) / "before"
        after = Path(directory) / "after"
        shutil.copytree(CASES / case, before)
        shutil.copytree(CASES / case, after)
        engine = UnificationRefactorEngine(min_lines=3, cross_module_helpers=True)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            results, _ = engine.refactor_directory_to_fixed_point(
                str(after / "pkg"), str(after / "pkg"), progress="none"
            )
        assert (
            results or case in REJECTED
        ), "Each fixture must exercise a real cross-file extraction"
        transformed = _python_files(after) != _python_files(before)
        assert transformed == (sum(applied for applied, _ in results.values()) > 0)
        assert _run(after) == _run(before)
        if transformed:
            # No module's public names appear, disappear, or change meaning.
            modules = _modules(before)
            assert module_faces(after, modules) == module_faces(before, modules)
        assert transformed == (case in TRANSFORMED), (
            "rejected" if not transformed else "transformed"
        )
