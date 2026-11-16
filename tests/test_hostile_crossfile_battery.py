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
the helper's own module. By default a helper takes no builtin parameter,
so cross-module builtin reads decline. ``BUILTIN_CALLER_LOOKUPS`` also runs
with the explicit parameterization mode to verify caller lookup thunks.

As in ``test_hostile_battery``, ``TRANSFORMED`` pins which packages the
current engine rewrites, so a lost cross-file extraction fails as loudly as
a wrong one. The lifetime-refusal profile preserves the semantic assertions
and pins each additional refusal's reason on uncertified interpreters.
Every package is in exactly one state. Rejected today:
``xf13_import_time_effects``, whose helper import would run a module that
prints at import time, which the borrower's own import never ran; the four
whose borrower rebinds ``len`` (``xf17``, ``xf18``, ``xf19``, ``xf22``); and
``xf23_relative_import_in_another_package``, whose subpackages ``pkg.x`` and
``pkg.y`` never import each other, so neither may gain an import of the
other (docs/design/DECISIONS.md, "Import names come from the program");
``xf7n_namesake_of_a_required_library``, whose ``zzlib/`` is a namesake of the
distribution its ``pyproject.toml`` requires;
``xf9xi_namesake_lacking_a_module``, whose ``third_party/zzlib/`` lacks the
``zzlib.core`` the program imports, so is not the ``zzlib`` it imports; the two
``xf7fz_extra_typed_read_before_bind_*``, whose block reads a name before
binding it; and ``xf7fz_late_toplevel_module_in_package``, whose ``pkg/c.py``
imports a module of ``pkg`` as a top-level name, so runs as a top-level
module itself.

The ``r9dc_`` packages are the round-4 audit's decorator cases. Ordinary
wrappers and star imports remain in the equivalence battery; body-recompiling
decorators are in ``REFLECTION_CASES`` and require extraction and valid syntax
without promising runtime preservation.

``r9sr_inline_snapshot`` holds the fourth audit's reproducer: two tests that
differ in the literal each passes to ``snapshot()``. A stub of inline-snapshot
beside ``pkg``, imported under the library's name, keys each call by its
position as the library does, and its ``run.py`` runs the tests under pytest.

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

``IMPORT_REFUSALS`` retains historical packages whose imports do not directly
establish that a helper host has loaded. Importing a new package member can
overwrite an ordinary parent attribute, and member-from imports do not prove
the member is a module. ``EXPLICIT_HOST_IMPORTS`` runs copied variants with
direct host loads so the original binding and typing defects still exercise
extraction while historical inputs remain unchanged.

A package in ``REFUSED`` holds a file using lazy imports, stored as
``.pynew`` (``tests.hostile_execution``). Before Python 3.15, strict mode must
refuse before writing, naming the unparseable file. Relaxed mode warns once,
leaves that file unchanged, and checks the portion that runs on this interpreter.
A separate newer-runtime test imports the skipped consumers: it checks output
preservation for the builtin patch, and successful execution for the documented
body-recompilation exclusion. On 3.15 the whole package is visible; the current
lifetime profile refuses these candidates and the program still runs unchanged. The two ``xf9up_`` packages are the
round-4 audit's P1-2 and P1-3, where such a file hid a decorator applied by
hand and a test's patch of ``len``.

A package runs with ``--cross-module`` unless ``WITHOUT_CROSS_MODULE`` names
it: then only duplicates within a module are paired, as ``towel dry`` pairs
them by default. One in ``TYPED`` runs with the checker its
``pyproject.toml`` configures, as ``towel dry`` does by default, and is
skipped where that checker is not installed. As in ``test_hostile_battery``,
a package in ``KNOWN_DEFECTS`` shows a defect an audit reported and is not
yet fixed: a strict expected failure whose transformed state is not pinned.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict

import pytest

from tests.compiler_golden_profiles import lifetime_refusal_profile
from tests.hostile_execution import (
    ISOLATED_ENV,
    ScopeWatch,
    copy_fixture_tree,
    module_faces,
    observe,
)
from tests.hostile_refactoring import OWNERSHIP_BOOKKEEPING, refactor_package
from tests.hostile_refactoring import with_known_defects
from tests.test_cli_integration import invoke
from towel.unification.exceptions import UnparsedProgramError
from towel.unification.refactor_engine import UnificationRefactorEngine

# The battery's pins are 1.949's: which packages strict mode transforms, and
# which its ownership proof refuses. Relaxed mode, the default, has its own
# pass below (test_relaxed_directory_refactoring_preserves_program_output).
pytestmark = pytest.mark.strict_mode

CASES = Path(__file__).parent / "hostile_crossfile"

# Each of these formerly read a builtin in the borrowed helper's host module.
# Default extraction now declines. The explicit mode preserves every ordinary
# runtime/scope/public-name observation; extraction also needs a proved host
# load, supplied by the copied variants when IMPORT_REFUSALS applies.
BUILTIN_CALLER_LOOKUPS = {
    "r9sb_lambda_capture_across_modules",
    "xf11_package_init_reaches_back",
    "xf12_cycle_through_package_init",
    "xf21_builtin_shadowed_in_third_module",
    "xf7fz_binding_x_class_level_name",
    "xf7fz_grammar_u0474_for_target_prebound",
    "xf7fz_grammar_u0624_for_target_prebound",
    "xf7fz_grammar_u1209_for_target_prebound",
    "xf7n_host_the_wheel_leaves_out",
    "xf7n_subpackage_the_wheel_leaves_out",
    "xf9_same_named_base_class",
    "xf9xi_type_only_import_of_a_missing_module",
}

HISTORICAL_EXTRACTIONS = {
    "xf1792_ci_strict_any",
    "xf1792_typed_shared_helper_comments",
    "xf1792_excluded_probe_import",
    "xf10_reuse_existing_function",
    "xf15_ancestor_in_another_module",
    "xf16_consumer_outside_target_owns_helper_name",
    "xf20_builtin_shadowed_in_ancestor_module",
    "xf26_relative_import_ancestor_in_another_package",
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
    # The round-3 audit's families (xf7fz_<family>_<case>): a sample of the
    # cross-module cases the audit found sound, every one transformed.
    "xf7fz_modules_init_hosts",
    "xf7fz_modules_script_main_guard",
    # Two modules pytest does not rewrite share an assert: either may host it.
    "xf7d_asserts_shared_by_modules_rewritten_alike",
    # Round-4 audit P2-02: a star import whose provider's __all__ cannot bind
    # staticmethod or property left both unknown.
    "r9dc_star_import_that_cannot_bind_a_decorator",
}

# Historical packages whose existing imports do not prove the candidate host
# loaded before the new helper import. A package member import may read an
# existing ordinary attribute, and a new submodule load would replace it.
# xf12 additionally encounters partially initialized ancestors; their later
# imports cannot establish readiness. These cases still run in both builtin
# modes, and retain runtime, scope and public-name assertions below.
IMPORT_REFUSALS = {
    "r9sb_lambda_capture_across_modules",
    "xf12_cycle_through_package_init",
    "xf14_script_with_leading_statement",
    "xf21_builtin_shadowed_in_third_module",
    "xf24_relative_import_climbs_elsewhere",
    "xf25_relative_import_in_the_same_package",
    "xf27_registration_decorator_in_host",
    "xf28_registration_decorator_in_reused_module",
    "xf3_same_named_local_function",
    "xf4_same_named_class",
    "xf5_dunder_file",
    "xf6_same_alias_different_import",
    "xf7_docstring_import",
    "xf7fz_binding_x_class_level_name",
    "xf7fz_binding_x_dunder_file",
    "xf7fz_builtins_x_shadow_in_b",
    "xf7fz_grammar_u0474_for_target_prebound",
    "xf7fz_grammar_u0624_for_target_prebound",
    "xf7fz_grammar_u1209_for_target_prebound",
    "xf7fz_modules_b_imports_a",
    "xf7fz_modules_rel_import_in_block",
    "xf7fz_modules_three_modules_cluster",
    "xf7n_host_the_wheel_leaves_out",
    "xf7n_subpackage_the_wheel_leaves_out",
    "xf8_helper_name_collision",
    "xf9_same_named_base_class",
    "xf9xi_type_only_import_of_a_missing_module",
}

# Packages the engine must leave alone, with the reason a comment in the fixture.
REJECTED = {
    *BUILTIN_CALLER_LOOKUPS,
    *IMPORT_REFUSALS,
    # TYPE_CHECKING is mutable: this host's guarded branches may execute.
    "xf29_type_checking_block_with_branches",
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
    # Round-4 audit P1-04 across modules: each block holds its function's only
    # binding of total, read on the early return (moves_only_binding).
    "xf9bd_only_binding_across_modules",
}

REFLECTION_CASES: Dict[str, str] = {
    "r9dc_decorator_rebound_by_another_module": "a rebound decorator recompiles function bodies",
    "r9dc_star_import_binds_an_instrumenting_decorator": "an imported decorator recompiles bodies",
    "r9dc_star_import_of_a_module_part_way_through_a_cycle": "an import cycle exposes a recompiler",
    "r9sr_inline_snapshot": "snapshot calls inspect their caller's source position",
    "xf7d_assert_moves_to_a_module_pytest_does_not_rewrite": "pytest rewrites only one module",
}

REFUSED: Dict[str, str] = {
    # Round-4 audit P1-2: pkg/fast.py applies a recompiling decorator to
    # kernels.first by hand, in syntax introduced by Python 3.15.
    "xf9up_newer_syntax_hides_hand_decoration": "pkg/fast.py",
    # Round-4 audit P1-3: tests/test_b.py patches len into pkg.b, likewise.
    "xf9up_newer_syntax_test_hides_builtin_patch": "tests/test_b.py",
}
"""Packages whose run Towel refuses before writing anything, with the file the refusal names."""

# Preserve the original import, checker and runtime fixtures. A proved host
# load does not also prove that a split frame can transfer owned locals on
# exceptional paths, or that every retained local is bound at block exit.
OWNERSHIP_REFUSALS: Dict[str, str] = {
    "r9dc_star_import_that_cannot_bind_a_decorator": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf10_reuse_existing_function": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf1792_typed_shared_helper_comments": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7d_asserts_shared_by_modules_rewritten_alike": "retained local is not definitely bound on every block exit",
    "xf7fz_extra_typed_binder_message_mypy": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_extra_typed_binder_message_pyright": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_modules_init_hosts": "retained local is not definitely bound on every block exit",
    "xf7fz_modules_script_main_guard": "retained local is not definitely bound on every block exit",
    "xf7fz_typeguard2_from_compat_true_both": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_typeguard2_from_compat_true_late_use_both": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_typeguard2_from_compat_true_late_use_mypy": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_typeguard2_from_compat_true_mypy": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_typeguard2_import_alias_flag_both": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_typeguard2_import_alias_flag_mypy": "owned bindings straddle a partial or uncertified whole-body frame boundary",
}
EXPLICIT_OWNERSHIP_REFUSALS: Dict[str, str] = {
    "xf21_builtin_shadowed_in_third_module": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf24_relative_import_climbs_elsewhere": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf25_relative_import_in_the_same_package": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf27_registration_decorator_in_host": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_binding_x_class_level_name": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_binding_x_dunder_file": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_builtins_x_shadow_in_b": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_grammar_u0624_for_target_prebound": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_grammar_u1209_for_target_prebound": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_modules_b_imports_a": "retained local is not definitely bound on every block exit",
    "xf7fz_modules_rel_import_in_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7fz_modules_three_modules_cluster": "retained local is not definitely bound on every block exit",
    "xf7n_host_the_wheel_leaves_out": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7n_subpackage_the_wheel_leaves_out": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf9xi_type_only_import_of_a_missing_module": "retained local is not definitely bound on every block exit",
}
CALLER_LOOKUP_OWNERSHIP_REFUSALS: Dict[str, str] = {
    "xf11_package_init_reaches_back": "owned bindings straddle a partial or uncertified whole-body frame boundary",
}
REFLECTION_OWNERSHIP_REFUSALS: Dict[str, str] = {
    "r9dc_star_import_binds_an_instrumenting_decorator": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r9dc_star_import_of_a_module_part_way_through_a_cycle": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "xf7d_assert_moves_to_a_module_pytest_does_not_rewrite": "owned bindings straddle a partial or uncertified whole-body frame boundary",
}

# These extractions need the original frame-release order. On uncertified
# interpreters their fixtures still assert output, scopes and the refusal code.
UNCERTIFIED_OWNERSHIP_REFUSALS = frozenset(
    {
        "xf16_consumer_outside_target_owns_helper_name",
        "xf1792_excluded_probe_import",
        "xf30_star_importer_takes_a_typing_name",
        "xf31_package_init_star_imports_a_matcher_named_any",
        "xf32_star_importer_outside_the_target",
        "xf7fz_extra_typed_thunk_lambda_default_mypy",
        "xf7fz_extra_typed_thunk_lambda_default_pyright",
        "xf7fz_misc_typed_for_prebound_mypy",
        "xf7t_import_order_is_registration_order",
    }
)
UNCERTIFIED_EXPLICIT_OWNERSHIP_REFUSALS = frozenset(
    {
        "r9sb_lambda_capture_across_modules",
        "xf28_registration_decorator_in_reused_module",
        "xf3_same_named_local_function",
        "xf4_same_named_class",
        "xf5_dunder_file",
        "xf6_same_alias_different_import",
        "xf7_docstring_import",
        "xf8_helper_name_collision",
    }
)
UNCERTIFIED_REFLECTION_OWNERSHIP_REFUSALS = frozenset({"r9dc_decorator_rebound_by_another_module"})
if lifetime_refusal_profile():
    OWNERSHIP_REFUSALS |= dict.fromkeys(
        UNCERTIFIED_OWNERSHIP_REFUSALS, "lifetime_order_uncertified"
    )
    EXPLICIT_OWNERSHIP_REFUSALS |= dict.fromkeys(
        UNCERTIFIED_EXPLICIT_OWNERSHIP_REFUSALS, "lifetime_order_uncertified"
    )
    REFLECTION_OWNERSHIP_REFUSALS |= dict.fromkeys(
        UNCERTIFIED_REFLECTION_OWNERSHIP_REFUSALS, "lifetime_order_uncertified"
    )

TRANSFORMED = HISTORICAL_EXTRACTIONS - OWNERSHIP_REFUSALS.keys()
REJECTED |= OWNERSHIP_REFUSALS.keys()

TYPED = frozenset(
    {
        "xf1792_ci_strict_any",
        "xf1792_typed_shared_helper_comments",
        "xf1792_excluded_probe_import",
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

WITHOUT_CROSS_MODULE = (TYPED - {"xf1792_typed_shared_helper_comments"}) | {
    "xf9up_newer_syntax_hides_hand_decoration",
    "r9sr_inline_snapshot",
}
"""Packages run without ``--cross-module``: the earlier typed fixtures, as the audit ran them, the
default-mode refusal, and the inline-snapshot fixture, whose defect the audit found without it."""

KNOWN_DEFECTS: Dict[str, str] = {}
"""Packages whose defect is reported and not yet fixed, each with its reason from
``tests/audit_defects.py``. None is open: every round-3 P1 package passes."""


def _python_files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*.py"))}


def _assert_lifetime_order_refusal(
    package: Path, *, cross_module: bool, parameterize_builtins: bool
) -> None:
    engine = UnificationRefactorEngine(
        min_lines=3,
        skip_trivial_helpers=False,
        cross_module_helpers=cross_module,
        parameterize_builtins=parameterize_builtins,
    )
    engine.analyze_directory(str(package.resolve()), progress="none")
    assert engine.declined_pairs.get("lifetime_order_uncertified", 0) > 0, engine.declined_pairs


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
        sorted(
            path.name
            for path in CASES.iterdir()
            if path.is_dir() and path.name not in REFLECTION_CASES
        ),
        KNOWN_DEFECTS,
    ),
)
def test_directory_refactoring_preserves_program_output(case: str) -> None:
    _check_directory_program_output(case, parameterize_builtins=False)


def _check_directory_program_output(
    case: str,
    *,
    parameterize_builtins: bool,
    explicit_imports: tuple[tuple[str, str], ...] = (),
) -> None:
    with tempfile.TemporaryDirectory(prefix="towel-hostile-xf-") as directory:
        before = Path(directory) / "before"
        after = Path(directory) / "after"
        copy_fixture_tree(CASES / case, before)
        copy_fixture_tree(CASES / case, after)
        for root in (before, after):
            for relative, module in explicit_imports:
                _add_leading_module_import(root / relative, module)
            if explicit_imports and case in EXPLICIT_COMMON_IMPORTS:
                # The copied grammar variant spells the byte-identical
                # common modules' six exports. A runtime star can overwrite
                # an early helper, a separate refusal from the binder bug.
                for name in ("a", "b"):
                    path = root / "pkg" / f"{name}.py"
                    source = path.read_text()
                    assert source.count("from .common import *") == 1
                    path.write_text(
                        source.replace(
                            "from .common import *",
                            "from .common import LOG, G, tr, Box, Ctx, bump",
                        )
                    )
        if case in REFUSED:
            try:
                ast.parse((before / REFUSED[case]).read_text())
            except SyntaxError:
                with pytest.raises(
                    UnparsedProgramError, match=re.escape(f"  {REFUSED[case]}: line ")
                ):
                    refactor_package(after / "pkg", cross_module=case not in WITHOUT_CROSS_MODULE)
            else:
                # The lazy import is now visible. It is outside the candidate
                # blocks; their owned locals, not parsing, prevent extraction.
                assert lifetime_refusal_profile()
                results = refactor_package(
                    after / "pkg", cross_module=case not in WITHOUT_CROSS_MODULE
                )
                assert sum(applied for applied, _ in results.values()) == 0
                _assert_lifetime_order_refusal(
                    after / "pkg",
                    cross_module=case not in WITHOUT_CROSS_MODULE,
                    parameterize_builtins=False,
                )
                assert _run(before)[0] == 0
            assert _python_files(after) == _python_files(before)
            assert _run(after) == _run(before)
            return
        scopes = ScopeWatch()
        results = refactor_package(
            after / "pkg",
            cross_module=case not in WITHOUT_CROSS_MODULE,
            typed=case in TYPED,
            file_finisher=scopes,
            parameterize_builtins=parameterize_builtins,
        )
        transformed = _python_files(after) != _python_files(before)
        assert transformed == (sum(applied for applied, _ in results.values()) > 0)
        # No change moves a name a kept function reads to another scope.
        assert scopes.found == []
        assert _run(after) == _run(before)
        uncertified_refusals = (
            UNCERTIFIED_EXPLICIT_OWNERSHIP_REFUSALS
            if explicit_imports
            else UNCERTIFIED_OWNERSHIP_REFUSALS
        )
        if lifetime_refusal_profile() and case in uncertified_refusals:
            _assert_lifetime_order_refusal(
                after / "pkg",
                cross_module=case not in WITHOUT_CROSS_MODULE,
                parameterize_builtins=parameterize_builtins,
            )
        if transformed:
            # No module's public names appear, disappear, or change meaning.
            modules = _modules(before)
            assert module_faces(after, modules) == module_faces(before, modules)
        if case not in KNOWN_DEFECTS:
            ordinary_refusal = (
                case in EXPLICIT_OWNERSHIP_REFUSALS
                if explicit_imports
                else (
                    case in CALLER_LOOKUP_OWNERSHIP_REFUSALS
                    if parameterize_builtins
                    else case in OWNERSHIP_REFUSALS
                )
            )
            # Pinned only once the defect is fixed: a fix that declines the
            # package must show as an XPASS, not fail here as expected.
            assert (
                results or case in REJECTED or ordinary_refusal
            ), "Each fixture must exercise a real cross-file extraction"
            assert transformed == (
                not ordinary_refusal
                and (
                    bool(explicit_imports)
                    or case in TRANSFORMED
                    or (
                        parameterize_builtins
                        and case in BUILTIN_CALLER_LOOKUPS
                        and case not in IMPORT_REFUSALS
                    )
                )
            ), ("rejected" if not transformed else "transformed")


def _parses(path: Path) -> bool:
    """Whether this interpreter parses ``path``: the REFUSED fixtures use syntax newer than 3.13."""
    try:
        ast.parse(path.read_text())
    except SyntaxError:
        return False
    return True


RELAXED_REFLECTION_WHERE_PARSED: Dict[str, str] = {
    "xf9up_newer_syntax_hides_hand_decoration": "pkg/fast.py recompiles kernels.first by hand",
}
"""REFUSED packages that are reflection cases once this interpreter parses their newer syntax.

On 3.11 to 3.14 strict mode refuses because pkg/fast.py uses PEP 810's
``lazy import`` and does not parse; relaxed mode skips that file. On 3.15 it parses, and the two modes part
for a reason the relaxed contract records, then the output changes for one
it excludes. Strict mode declines: the helper would hand back the loop
variable ``i`` to keep its object alive, and ``i`` is unbound when the loop
does not run (``conditionally_bound_return``; on 3.15 also the release-order
refusal above). Relaxed mode, by its finalization assumption, returns only
names later code reads (docs/design/DECISIONS.md, "Relaxed mode is the
default; strict mode keeps exact lifetimes"), so it extracts first's loop.
pkg/fast.py then recompiles ``kernels.first`` from its source, rewriting
``i * 2``, and finds only the helper's call there: reflection, outside the
contract in both modes, as strict mode's own extraction from
r7d_instrumenting_call_applied_by_hand shows. Only the recompiled function's
output may change (``_assert_only_the_recompiled_function_changed``).
"""


def _assert_only_the_recompiled_function_changed(before: Path, after: Path) -> None:
    """The one output line that differs is ``fast_first``, which now computes first unrewritten."""
    code_before, out_before, err_before = _run(before)
    code_after, out_after, err_after = _run(after)
    assert (code_after, err_after) == (code_before, err_before)
    assert out_before == "first 13 second -13\nfast_first 121\n"
    # 13 is first(4): the recompiled source holds no ``i * 2`` to rewrite.
    assert out_after == "first 13 second -13\nfast_first 13\n"
    first = next(
        node
        for node in ast.parse((after / "pkg" / "kernels.py").read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "first"
    )
    assert "i * 2" not in ast.unparse(first)


RELAXED_OBSERVES_FINALIZATION: Dict[str, str] = {}
"""Packages whose output differs in relaxed mode because they print when an object is finalized.

As in ``test_hostile_battery``: relaxed mode assumes a program does not
depend on when objects are finalized once it has stopped using them
(docs/design/DECISIONS.md, "Relaxed mode is the default; strict mode keeps exact
lifetimes"). No cross-file fixture watches a finalizer, so the list is
empty, and any difference in relaxed mode is a defect to fix.
"""


@pytest.mark.parametrize(
    "case",
    sorted(
        path.name for path in CASES.iterdir() if path.is_dir() and path.name not in REFLECTION_CASES
    ),
)
def test_relaxed_directory_refactoring_preserves_program_output(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The same battery in relaxed mode: identical output, and no ownership bookkeeping."""
    with tempfile.TemporaryDirectory(prefix="towel-hostile-xf-relaxed-") as directory:
        before = Path(directory) / "before"
        after = Path(directory) / "after"
        copy_fixture_tree(CASES / case, before)
        copy_fixture_tree(CASES / case, after)
        cross_module = case not in WITHOUT_CROSS_MODULE
        unparsed = case in REFUSED and not _parses(before / REFUSED[case])
        scopes = ScopeWatch()
        results = refactor_package(
            after / "pkg",
            cross_module=cross_module,
            typed=case in TYPED,
            file_finisher=scopes,
            strict=False,
        )
        written, original = _python_files(after), _python_files(before)
        if unparsed:
            # Runtime item 6: strict's refusal remains tested above. Relaxed
            # mode must warn once and preserve the skipped bytes. The checks
            # below cover what this interpreter can execute; the newer-runtime
            # test below also executes the skipped consumer.
            assert written[REFUSED[case]] == original[REFUSED[case]]
            warnings = [
                record.message for record in caplog.records if "Skipping " in record.message
            ]
            assert len(warnings) == 1, warnings
            assert REFUSED[case] in warnings[0]
        transformed = written != original
        assert transformed == (sum(applied for applied, _ in results.values()) > 0)
        assert scopes.found == []
        for name, text in written.items():
            assert not OWNERSHIP_BOOKKEEPING.search(text) or (
                OWNERSHIP_BOOKKEEPING.search(original[name])
            ), name
        if case in RELAXED_OBSERVES_FINALIZATION:
            assert _run(after) != _run(before), "no longer differs: remove it from the list"
        elif case in RELAXED_REFLECTION_WHERE_PARSED and not unparsed:
            # Outside the preservation contract, as REFLECTION_CASES are: the
            # extraction has to be valid Python, and only the function the
            # program recompiles from its source may print something else.
            assert transformed, RELAXED_REFLECTION_WHERE_PARSED[case]
            for path in (after / "pkg").rglob("*.py"):
                compile(path.read_bytes(), str(path), "exec")
            _assert_only_the_recompiled_function_changed(before, after)
        else:
            assert _run(after) == _run(before)
        if transformed:
            modules = _modules(before)
            assert module_faces(after, modules) == module_faces(before, modules)


@pytest.mark.parametrize("case", sorted(BUILTIN_CALLER_LOOKUPS))
def test_caller_lookup_mode_preserves_cross_module_program_output(case: str) -> None:
    _check_directory_program_output(case, parameterize_builtins=True)


# Keep the original fixtures as regression evidence. These copied variants
# explicitly load the host before the borrower's definitions, so the original
# binding, grammar, registration and packaging fixes still exercise extraction.
EXPLICIT_HOST_IMPORTS: dict[str, tuple[tuple[str, str], ...]] = {
    "r9sb_lambda_capture_across_modules": (("pkg/quotes.py", "pkg.orders"),),
    "xf3_same_named_local_function": (("pkg/b.py", "pkg.a"),),
    "xf4_same_named_class": (("pkg/b.py", "pkg.a"),),
    "xf5_dunder_file": (("pkg/b.py", "pkg.a"),),
    "xf6_same_alias_different_import": (("pkg/b.py", "pkg.a"),),
    "xf7_docstring_import": (("pkg/b.py", "pkg.a"),),
    "xf8_helper_name_collision": tuple((f"pkg/{name}.py", "pkg.a") for name in "bcde"),
    "xf21_builtin_shadowed_in_third_module": (("pkg/b.py", "pkg.a"), ("pkg/c.py", "pkg.a")),
    "xf24_relative_import_climbs_elsewhere": (("pkg/x/a.py", "pkg.b"),),
    "xf25_relative_import_in_the_same_package": (("pkg/x/b.py", "pkg.x.a"),),
    "xf27_registration_decorator_in_host": (("pkg/beta.py", "pkg.alpha"),),
    "xf28_registration_decorator_in_reused_module": (("pkg/beta.py", "pkg.alpha"),),
    "xf7fz_binding_x_class_level_name": (("pkg/b.py", "pkg.a"),),
    "xf7fz_binding_x_dunder_file": (("pkg/b.py", "pkg.a"),),
    "xf7fz_builtins_x_shadow_in_b": (("pkg/b.py", "pkg.a"),),
    "xf7fz_grammar_u0474_for_target_prebound": (("pkg/b.py", "pkg.a"),),
    "xf7fz_grammar_u0624_for_target_prebound": (("pkg/b.py", "pkg.a"),),
    "xf7fz_grammar_u1209_for_target_prebound": (("pkg/b.py", "pkg.a"),),
    "xf7fz_modules_b_imports_a": (("pkg/b.py", "pkg.a"),),
    "xf7fz_modules_rel_import_in_block": (("pkg/b.py", "pkg.a"),),
    "xf7fz_modules_three_modules_cluster": (("pkg/b.py", "pkg.a"), ("pkg/c.py", "pkg.a")),
    "xf7n_host_the_wheel_leaves_out": (("pkg/src/shop/_devtools.py", "shop.stats"),),
    "xf7n_subpackage_the_wheel_leaves_out": (("pkg/src/shop/devtools/dump.py", "shop.stats"),),
    "xf9_same_named_base_class": (
        ("pkg/one/revoke.py", "pkg.one.introspect"),
        ("pkg/two/access.py", "pkg.one.introspect"),
        ("pkg/two/request.py", "pkg.one.introspect"),
    ),
    "xf9xi_type_only_import_of_a_missing_module": (("pkg/b.py", "pkg.a"),),
}

EXPLICIT_COMMON_IMPORTS = frozenset(
    {
        "xf7fz_grammar_u0474_for_target_prebound",
        "xf7fz_grammar_u0624_for_target_prebound",
        "xf7fz_grammar_u1209_for_target_prebound",
    }
)


def _add_leading_module_import(path: Path, module: str) -> None:
    source = path.read_text()
    tree = ast.parse(source)
    position = tree.body[0].lineno - 1 if tree.body else 0
    for index, statement in enumerate(tree.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ) or (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module == "__future__"
        ):
            position = statement.end_lineno or statement.lineno
        else:
            break
    lines = source.splitlines(keepends=True)
    lines.insert(position, f"import {module} as _loaded_host\n")
    path.write_text("".join(lines))


@pytest.mark.parametrize("case", sorted(EXPLICIT_HOST_IMPORTS))
def test_explicit_host_load_keeps_the_original_cross_module_regression_active(case: str) -> None:
    _check_directory_program_output(
        case,
        parameterize_builtins=case in BUILTIN_CALLER_LOOKUPS,
        explicit_imports=EXPLICIT_HOST_IMPORTS[case],
    )


@pytest.mark.parametrize("case", sorted(REFLECTION_CASES))
def test_instrumentation_does_not_veto_directory_extraction(tmp_path: Path, case: str) -> None:
    root = tmp_path / "project"
    copy_fixture_tree(CASES / case, root)
    scopes = ScopeWatch()
    results = refactor_package(
        root / "pkg",
        cross_module=case not in WITHOUT_CROSS_MODULE,
        file_finisher=scopes,
        parameterize_builtins=True,
    )
    applied = sum(applied for applied, _ in results.values())
    if lifetime_refusal_profile() and case in UNCERTIFIED_REFLECTION_OWNERSHIP_REFUSALS:
        _assert_lifetime_order_refusal(
            root / "pkg",
            cross_module=case not in WITHOUT_CROSS_MODULE,
            parameterize_builtins=True,
        )
    if case in REFLECTION_OWNERSHIP_REFUSALS:
        assert applied == 0, REFLECTION_OWNERSHIP_REFUSALS[case]
    else:
        assert applied > 0, REFLECTION_CASES[case]
    assert scopes.found == []
    for path in (root / "pkg").rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")


def test_cli_refuses_a_top_level_module_inside_the_package(tmp_path: Path) -> None:
    """``pkg/c.py`` imports ``helpers_top``, a module file of ``pkg`` itself, top-level.

    The name ``helpers_top`` then belongs to the package and to the top level
    at once, so a ``--cross-module`` run refuses before it writes anything, as
    it refuses a top-level package inside the package (docs/design/DECISIONS.md,
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


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_relaxed_skipped_consumer_executes_on_newer_python(case: str, tmp_path: Path) -> None:
    """Review suspicion: import skipped code on a runtime that actually understands it."""
    before, after = tmp_path / "before", tmp_path / "after"
    copy_fixture_tree(CASES / case, before)
    copy_fixture_tree(CASES / case, after)
    if _parses(before / REFUSED[case]):
        pytest.skip("this interpreter parses the consumer; the ordinary battery executes it")
    interpreter = shutil.which("python3.15")
    if interpreter is None:
        pytest.skip("Python 3.15 is required to execute the skipped lazy-import consumer")

    def run(root: Path) -> tuple[int, str, list[str]]:
        completed = subprocess.run(
            [interpreter, "run.py"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=ISOLATED_ENV,
        )
        return completed.returncode, completed.stdout, completed.stderr.strip().splitlines()[-1:]

    original = run(before)
    assert original[0] == 0, original
    assert "needs a newer Python" not in original[1], original
    assert ("fast_first" if case in RELAXED_REFLECTION_WHERE_PARSED else "patched") in original[1]
    results = refactor_package(after / "pkg", strict=False)
    changed = run(after)
    assert changed[0] == 0, changed
    assert "needs a newer Python" not in changed[1], changed
    assert (after / REFUSED[case]).read_bytes() == (before / REFUSED[case]).read_bytes()
    if case in RELAXED_REFLECTION_WHERE_PARSED:
        # instrumented() calls inspect.getsource, rewrites the function text,
        # and execs it. Source reflection is already outside the preservation
        # contract even when Towel's own interpreter can parse the consumer.
        assert sum(applied for applied, _ in results.values()) > 0
        assert "fast_first" in changed[1]
    else:
        assert changed == original
