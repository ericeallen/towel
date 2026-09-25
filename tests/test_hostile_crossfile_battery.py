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
other (docs/DECISIONS.md, "Import names come from the program"); and
``xf7n_namesake_of_a_required_library``, whose ``zzlib/`` is a namesake of the
distribution its ``pyproject.toml`` requires.

A fixture that configures an import sorter is refactored with it, as the
command line would: ``xf7t_import_order_is_registration_order`` holds a
module the sorter's configuration excludes and one whose imports are not in
its order, and each import registers a plugin.

Three ``xf7n_`` fixtures are projects whose ``run.py`` runs the program as
it ships rather than from the tree, since only there does their defect
show: the namesake imports ``zzapp`` beside the installed ``zzlib``;
``xf7n_host_the_wheel_leaves_out`` and ``xf7n_subpackage_the_wheel_leaves_out``
import ``shop`` without the module hatch, or the subpackage setuptools,
leaves out of the wheel. Their modules left out may borrow from the ones
that ship, never the reverse.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import shutil
import tempfile

import pytest

from tests.hostile_execution import module_faces, observe
from towel.formatting import import_sorter_for_project
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
    # The host's new binding for its annotations is private, so no module that
    # star-imports it takes a typing name in place of its own Any or Callable:
    # a sibling (xf30), the package's __init__ (xf31), a module outside the
    # package the run was given (xf32).
    "xf30_star_importer_takes_a_typing_name",
    "xf31_package_init_star_imports_a_matcher_named_any",
    "xf32_star_importer_outside_the_target",
    # Only the benign module's twins; the rebinding hazard in the other keeps
    # its code (round-3 audit, P1-1).
    "xf7c_rebinding_enclosing_function_beside_a_twin_in_another_module",
    "xf7t_import_order_is_registration_order",
    "xf7n_host_the_wheel_leaves_out",
    "xf7n_subpackage_the_wheel_leaves_out",
}

# Packages the engine must leave alone, with the reason a comment in the fixture.
REJECTED = {
    "xf13_import_time_effects",
    "xf17_builtin_shadowed_in_borrower",
    "xf18_builtin_shadowed_by_star_import",
    "xf19_builtin_shadowed_by_borrower_local",
    "xf22_borrower_rebinds_builtins_namespace",
    "xf23_relative_import_in_another_package",
    "xf7n_namesake_of_a_required_library",
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
        engine = UnificationRefactorEngine(
            min_lines=3,
            cross_module_helpers=True,
            file_finisher=import_sorter_for_project(after / "pkg").tool,
        )
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
