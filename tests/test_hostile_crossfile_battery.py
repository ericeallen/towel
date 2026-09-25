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

import pytest

from tests.audit_defects import (
    P1_1_PREBOUND_REBINDING,
    P1_2_SEMICOLON_LINE,
    P1_3_LEADING_THUNK,
    P1_4_ANNOTATION_IMPORT,
    P1_5_RENAMED_BINDER,
    P1_6_TOP_LEVEL_INSIDE_PACKAGE,
    P1_7_READ_BEFORE_BIND,
)
from tests.hostile_execution import observe
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

_TYPED_DEFECTS = {
    # The round-3 audit's typed P1 cases: each Towel refactors in the typed
    # mode, with the strict checker its pyproject.toml configures, and the
    # checker accepts the change of behaviour.
    "xf7fz_misc_typed_for_prebound_mypy": P1_1_PREBOUND_REBINDING,
    "xf7fz_grammar_t11094_semicolon": P1_2_SEMICOLON_LINE,
    "xf7fz_grammar_t11271_semicolon": P1_2_SEMICOLON_LINE,
    "xf7fz_extra_typed_thunk_lambda_default_mypy": P1_3_LEADING_THUNK,
    "xf7fz_extra_typed_thunk_lambda_default_pyright": P1_3_LEADING_THUNK,
    "xf7fz_typeguard2_from_compat_true_mypy": P1_4_ANNOTATION_IMPORT,
    "xf7fz_typeguard2_from_compat_true_both": P1_4_ANNOTATION_IMPORT,
    "xf7fz_typeguard2_from_compat_true_late_use_mypy": P1_4_ANNOTATION_IMPORT,
    "xf7fz_typeguard2_from_compat_true_late_use_both": P1_4_ANNOTATION_IMPORT,
    "xf7fz_typeguard2_import_alias_flag_mypy": P1_4_ANNOTATION_IMPORT,
    "xf7fz_typeguard2_import_alias_flag_both": P1_4_ANNOTATION_IMPORT,
    "xf7fz_extra_typed_binder_message_mypy": P1_5_RENAMED_BINDER,
    "xf7fz_extra_typed_binder_message_pyright": P1_5_RENAMED_BINDER,
    "xf7fz_extra_typed_read_before_bind_mypy": P1_7_READ_BEFORE_BIND,
    "xf7fz_extra_typed_read_before_bind_pyright": P1_7_READ_BEFORE_BIND,
}

KNOWN_DEFECTS = {
    **_TYPED_DEFECTS,
    # The round-3 audit's cross-module P1 cases.
    "xf7fz_grammar_u0474_for_target_prebound": P1_1_PREBOUND_REBINDING,
    "xf7fz_grammar_u0624_for_target_prebound": P1_1_PREBOUND_REBINDING,
    "xf7fz_grammar_u1209_for_target_prebound": P1_1_PREBOUND_REBINDING,
    # The decided behaviour is the refusal that towel dry makes before it
    # writes anything (test_cli_refuses_a_top_level_module_inside_the_package);
    # the library entry this battery runs has no such check, so this entry
    # stays until the engine also leaves the importing module alone.
    "xf7fz_late_toplevel_module_in_package": P1_6_TOP_LEVEL_INSIDE_PACKAGE,
}

WITHOUT_CROSS_MODULE = frozenset(_TYPED_DEFECTS)
"""Packages run without ``--cross-module``, as the typed cases the audit reported ran."""

TYPED = frozenset(_TYPED_DEFECTS)
"""Packages run with the checker their ``pyproject.toml`` configures."""


def _python_files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*.py"))}


def _run(root: Path) -> tuple[int, str, list[str]]:
    return observe("run.py", root)


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
        assert (
            results or case in REJECTED
        ), "Each fixture must exercise a real cross-file extraction"
        transformed = _python_files(after) != _python_files(before)
        assert transformed == (sum(applied for applied, _ in results.values()) > 0)
        assert _run(after) == _run(before)
        if case not in KNOWN_DEFECTS:
            assert transformed == (case in TRANSFORMED), (
                "rejected" if not transformed else "transformed"
            )


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=P1_6_TOP_LEVEL_INSIDE_PACKAGE)
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
