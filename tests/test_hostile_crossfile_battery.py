# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
other (docs/DECISIONS.md, "Import names come from the program");
``xf7n_namesake_of_a_required_library``, whose ``zzlib/`` is a namesake of the
distribution its ``pyproject.toml`` requires;
``xf9xi_namesake_lacking_a_module``, whose ``third_party/zzlib/`` lacks the
``zzlib.core`` the program imports, so is not the ``zzlib`` it imports; the two
``xf7fz_extra_typed_read_before_bind_*``, whose block reads a name before
binding it; and ``xf7fz_late_toplevel_module_in_package``, whose ``pkg/c.py``
imports a module of ``pkg`` as a top-level name, so runs as a top-level
module itself.

``xf7d_assert_moves_to_a_module_pytest_does_not_rewrite`` shares a block that
holds an assert pytest rewrites in one module and not in the other; its
``run.py`` runs pytest and prints each failing assert's message.

A fixture that configures an import sorter is refactored with it, as the
command line would: ``xf7t_import_order_is_registration_order`` holds a
module the sorter's configuration excludes and one whose imports are not in
its order, and each import registers a plugin.

Three ``xf7n_`` fixtures, and ``xf9xi_namesake_lacking_a_module``, are
projects whose ``run.py`` runs the program as it ships rather than from the
tree, since only there does their defect show: the namesakes import
``zzapp`` beside the installed ``zzlib``;
``xf7n_host_the_wheel_leaves_out`` and ``xf7n_subpackage_the_wheel_leaves_out``
import ``shop`` without the module hatch, or the subpackage setuptools,
leaves out of the wheel. Their modules left out may borrow from the ones
that ship, never the reverse.

A package runs with ``--cross-module`` unless ``WITHOUT_CROSS_MODULE`` names
it: then only duplicates within a module are paired, as ``towel dry`` pairs
them by default. One in ``TYPED`` runs with the checker its
``pyproject.toml`` configures, as ``towel dry`` does by default, and is
skipped where that checker is not installed. As in ``test_hostile_battery``,
a package in ``KNOWN_DEFECTS`` shows a defect an audit reported and is not
yet fixed: a strict expected failure whose transformed state is not pinned.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from typing import Dict

import pytest

from tests.hostile_execution import module_faces, observe
from tests.hostile_refactoring import refactor_package, with_known_defects
from tests.test_cli_integration import invoke

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
    # The round-3 audit's P1 cases, typed and across modules, each now
    # extracted soundly by the fix of its defect (and, typed, accepted by
    # the strict checker its pyproject.toml configures).
    "xf7fz_misc_typed_for_prebound_mypy",
    "xf7fz_grammar_t11094_semicolon",
    "xf7fz_grammar_t11271_semicolon",
    "xf7fz_extra_typed_thunk_lambda_default_mypy",
    "xf7fz_extra_typed_thunk_lambda_default_pyright",
    "xf7fz_typeguard2_from_compat_true_mypy",
    "xf7fz_typeguard2_from_compat_true_both",
    "xf7fz_typeguard2_from_compat_true_late_use_mypy",
    "xf7fz_typeguard2_from_compat_true_late_use_both",
    "xf7fz_typeguard2_import_alias_flag_mypy",
    "xf7fz_typeguard2_import_alias_flag_both",
    "xf7fz_extra_typed_binder_message_mypy",
    "xf7fz_extra_typed_binder_message_pyright",
    "xf7fz_grammar_u0474_for_target_prebound",
    "xf7fz_grammar_u0624_for_target_prebound",
    "xf7fz_grammar_u1209_for_target_prebound",
    # The round-3 audit's families (xf7fz_<family>_<case>): a sample of the
    # cross-module cases the audit found sound, every one transformed.
    "xf7fz_binding_x_class_level_name",
    "xf7fz_binding_x_dunder_file",
    "xf7fz_builtins_x_shadow_in_b",
    "xf7fz_modules_b_imports_a",
    "xf7fz_modules_init_hosts",
    "xf7fz_modules_rel_import_in_block",
    "xf7fz_modules_script_main_guard",
    "xf7fz_modules_three_modules_cluster",
    # Two modules pytest does not rewrite share an assert: either may host it.
    "xf7d_asserts_shared_by_modules_rewritten_alike",
    # A type-only import of a module the tree lacks never runs, so its file
    # still shares helpers (round-4 audit, p2_typechecking_missing).
    "xf9xi_type_only_import_of_a_missing_module",
    # Round-4 P1-03 across modules: the lambda keeps its own parameter.
    "r9sb_lambda_capture_across_modules",
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
    # Round-4 audit P1-3: the program imports a zzlib.core that third_party/zzlib
    # lacks, so the directory sharing zzlib.utils with the library is in doubt.
    "xf9xi_namesake_lacking_a_module",
    # Round-3 audit P1-7: the block reads scale before binding it, which only
    # the original's UnboundLocalError shows (incomplete_lifetime_block1).
    "xf7fz_extra_typed_read_before_bind_mypy",
    "xf7fz_extra_typed_read_before_bind_pyright",
    # Round-3 audit P1-6: helpers_top, imported top-level by pkg/c.py, is a
    # module of pkg, so c.py runs as a top-level module and is given no
    # import; towel dry refuses the run outright
    # (test_cli_refuses_a_top_level_module_inside_the_package).
    "xf7fz_late_toplevel_module_in_package",
    # A test module's assert would move to a module pytest does not rewrite,
    # and the AssertionError pytest reports would lose its explanation.
    "xf7d_assert_moves_to_a_module_pytest_does_not_rewrite",
}

TYPED = frozenset(
    {
        # The round-3 audit's typed cases: refactored with the strict checker
        # the fixture's pyproject.toml configures, as towel dry does by default.
        "xf7fz_misc_typed_for_prebound_mypy",
        "xf7fz_grammar_t11094_semicolon",
        "xf7fz_grammar_t11271_semicolon",
        "xf7fz_extra_typed_thunk_lambda_default_mypy",
        "xf7fz_extra_typed_thunk_lambda_default_pyright",
        "xf7fz_typeguard2_from_compat_true_mypy",
        "xf7fz_typeguard2_from_compat_true_both",
        "xf7fz_typeguard2_from_compat_true_late_use_mypy",
        "xf7fz_typeguard2_from_compat_true_late_use_both",
        "xf7fz_typeguard2_import_alias_flag_mypy",
        "xf7fz_typeguard2_import_alias_flag_both",
        "xf7fz_extra_typed_binder_message_mypy",
        "xf7fz_extra_typed_binder_message_pyright",
        "xf7fz_extra_typed_read_before_bind_mypy",
        "xf7fz_extra_typed_read_before_bind_pyright",
    }
)
"""Packages run with the checker their ``pyproject.toml`` configures."""

WITHOUT_CROSS_MODULE = TYPED
"""Packages run without ``--cross-module``: every typed one so far, as the audit ran them."""

KNOWN_DEFECTS: Dict[str, str] = {}
"""Packages whose defect is reported and not yet fixed, each with its reason from
``tests/audit_defects.py``. None is open: every round-3 P1 package passes."""


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


@pytest.mark.parametrize(
    "case",
    with_known_defects(
        sorted(path.name for path in CASES.iterdir() if path.is_dir()), KNOWN_DEFECTS
    ),
)
def test_directory_refactoring_preserves_program_output(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix="towel-hostile-xf-") as directory:
        before = Path(directory) / "before"
        after = Path(directory) / "after"
        shutil.copytree(CASES / case, before)
        shutil.copytree(CASES / case, after)
        results = refactor_package(
            after / "pkg", cross_module=case not in WITHOUT_CROSS_MODULE, typed=case in TYPED
        )
        transformed = _python_files(after) != _python_files(before)
        assert transformed == (sum(applied for applied, _ in results.values()) > 0)
        assert _run(after) == _run(before)
        if transformed:
            # No module's public names appear, disappear, or change meaning.
            modules = _modules(before)
            assert module_faces(after, modules) == module_faces(before, modules)
        if case not in KNOWN_DEFECTS:
            # Pinned only once the defect is fixed: a fix that declines the
            # package must show as an XPASS, not fail here as expected.
            assert (
                results or case in REJECTED
            ), "Each fixture must exercise a real cross-file extraction"
            assert transformed == (case in TRANSFORMED), (
                "rejected" if not transformed else "transformed"
            )


def test_cli_refuses_a_top_level_module_inside_the_package(tmp_path: Path) -> None:
    """``pkg/c.py`` imports ``helpers_top``, a module file of ``pkg`` itself, top-level.

    The name ``helpers_top`` then belongs to the package and to the top level
    at once, so a ``--cross-module`` run refuses before it writes anything, as
    it refuses a top-level package inside the package (docs/DECISIONS.md,
    "Import names come from the program").
    """
    root = tmp_path.resolve() / "project"
    shutil.copytree(CASES / "xf7fz_late_toplevel_module_in_package", root)
    before = _python_files(root)
    package = str(root / "pkg")
    options = ["--cross-module", "--no-types", "--no-format", "--no-interactive"]
    ran = invoke(["dry", package, package, *options, "--progress", "none"])
    assert ran.status == 1, ran.stdout + ran.stderr
    assert "Refusing to share helpers across the modules of" in ran.stderr, ran.stderr
    assert _python_files(root) == before
