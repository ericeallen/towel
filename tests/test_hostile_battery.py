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

"""Execute hostile fixtures before and after fixed-point refactoring.

Each fixture in ``tests/hostile_cases`` is a script whose ``__main__`` block
prints every observation that an extraction could disturb: evaluation order,
evaluation count, conditional evaluation, closure cells, deletion, and
pattern bindings. The battery asserts that the program's output is identical
after refactoring, and that the module shows its importers the same public
names bound to the same things (``module_faces``), and records per fixture
whether the current engine transforms it or rejects it, so a change in either
direction is visible.

A fixture in ``KNOWN_DEFECTS`` shows a defect an audit reported and is not
yet fixed: it is expected to fail, strictly, and its transformed state is
not pinned, since the fix may extract it soundly or decline it. When the fix
lands the fixture passes, pytest reports the XPASS as a failure, and the
fixture moves from ``KNOWN_DEFECTS`` to ``TRANSFORMED`` or stays out of both.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from typing import Dict

import pytest

from tests.hostile_execution import module_faces, observe, parsed_or_skipped
from tests.hostile_refactoring import refactor_script, with_known_defects

CASES = Path(__file__).parent / "hostile_cases"

TRANSFORMED = {
    "r101_same_named_method_forces_module_helper",
    "h04_closure_freevar",
    "h05_cond_return_plus_retvar",
    "h13_dunder_file",
    "h26_match_capture",
    "h39_augassign_after_block",
    "h52_del_after_block",
    "h54_attr_param_property",
    "h57_exception_in_param",
    "r01_property_thunk_order",
    "r02_cluster_attrs",
    "r14_starred_param",
    "r22_fstring_attr",
    "r27_del_subscript",
    "r32_method_callee",
    "r38_name_two_positions",
    "r39_cluster_alpha",
    "r40_class_hierarchy",
    "r44_cluster_nested_overlap",
    "r45_async_no_await",
    "r46_nonlocal_counter",
    "r47_lru_cache_method",
    "r54_with_binding_used_after",
    "r55_chained_assign",
    "h10_short_circuit",
    "r66_conditional_callee",
    "r60_local_import",
    "r146_module_function_reads_caller_frame",
    "r144_class_cell_in_a_method_helper",
    "r142_module_data_rebound_by_callback",
    "r143_module_name_shadowed_by_a_local_elsewhere",
    "r62_recursion",
    "r140_unbound_name_on_untaken_branch",
    "r141_module_name_bound_after_an_early_call",
    "r63_except_var_used_in_handler",
    "r74_starred_vs_plain",
    "r80_inlined_leading_thunk",
    "r81_conditionally_bound_free_variable",
    "r82_local_classes_same_name",
    "r83_tab_indented_class",
    "r84_warn_stacklevel",
    "r87_nested_function_in_method",
    "r88_elif_branch",
    "r90_cluster_across_classes",
    "r91_class_body_helper",
    "r92_parameterless_class_body_helper",
    "r95_type_checking_annotation",
    "r97_helper_in_function_with_outside_site",
    "r57_star_unpack",
    "r98_same_named_methods_nested_helpers",
    "r99_partial_return_branches",
    "r100_clustered_site_with_live_binding",
    "r102_whole_body_reuse",
    "r103_lambda_parameter_spelling",
    "r104_walrus_target_spelling",
    # The unbound name of each site is thunked, so the helper reads it only
    # in the branch the original took.
    "r107_unbound_global_argument",
    # Four sites assign what the helper returns; two read a second name too.
    "r108_clustered_sites_assign_the_returned_name",
    # Blocks that bind a variable read afterwards, returned by the helper and
    # rebound by the generated call.
    "h01_side_effect_order",
    "h61_str_method_param",
    "r50_helper_name_collision",
    # A class whose __getattr__ serves every unknown name from its data keeps
    # serving the helper's old name once the helper is class-private.
    "r158_getattr_serves_unknown_names_from_data",
    "r69_annassign",
    "r76_return_order",
    "r94_import_binds_live_name",
    "r96_unpacked_targets_live",
    # Fifth audit: the eager-argument rule on flow, the comprehension scope, object
    # lifetimes, and the line table; each transformed soundly.
    "r109_eager_optional_import_failed",
    "r110_eager_type_checking_import",
    "r111_eager_module_except_as_name",
    "r112_eager_module_match_capture_unmatched",
    "r113_eager_class_attr_in_method",
    "r114_eager_nested_class_attr",
    "r115_eager_class_in_function_attr",
    "r116_eager_enclosing_bound_after_inner_call",
    "r117_eager_enclosing_deleted_before_inner_call",
    "r118_eager_enclosing_conditionally_bound",
    "r119_eager_module_def_and_class_after_call",
    "r120_eager_module_import_after_call",
    "r121_eager_comprehension_variable",
    "r122_eager_lambda_parameter",
    "r130_resource_observed_after_block_differs",
    "r131_weakref_and_id_identity",
    "r133_formfeed",
    "r134_u2028_in_comment_shifts_splice",
    "r135_formfeed_line_shifts_splice",
    "r136_u2028_in_string_literal",
    "r137_x1c_x85_in_comment",
    "r138_u2028_comment_same_indent_neighbours",
    "r139_literal_roundtrip",
    # Methods that never read their receiver: the helper is a module-level
    # function, since nothing a method can spell is sure to reach its class.
    "p01_static_helper_class_name_shadowed_by_parameter",
    "p02_static_helper_class_deleted",
    "p03_static_helper_mangled_class_name",
    "p04_static_helper_class_decorator_returns_factory",
    "p05_static_helper_class_global_rebound",
    "p06_static_helper_called_while_class_body_runs",
    "p07_static_methods_called_while_class_body_runs",
    "p08_classmethod_never_using_cls",
    # Classes that cannot take a helper into their body: it goes to module level.
    "p10_one_line_exception_base",
    "p11_one_line_base_docstring_and_assignment",
    "p12_one_line_body_after_a_split_header",
    # The positive control: every decorator here keeps the helper a method.
    "p18_known_class_decorators_keep_the_helper",
    # The helper is placed before the assignment that calls it, its annotations
    # quoted, rather than after the class they name.
    "p21_helper_placed_after_an_assignment_that_calls_it",
    # The blocks holding a literal generated code cannot spell stay; the run
    # goes on and extracts the ordinary duplicate beside them.
    "r147_wide_int_literal",
    # Equal constants of different types (0 and 0.0, True and 1) are passed as
    # arguments; a False elsewhere in a block no longer counts as its 0.
    "r148_equal_constants_of_different_types",
    # The names a pattern evaluates (a class, a dotted value, a mapping key)
    # are the helper's arguments.
    "r149_match_pattern_reads_caller_names",
    # A pattern's class, or the root of its dotted name, may differ: the
    # parameter's bare name keeps the pattern's meaning. (r150, where a
    # literal or a whole dotted name differs, is declined: a bare name there
    # would be a capture.)
    "r151_pattern_names_that_may_differ",
    # What a definition evaluates where it stands (defaults, decorators,
    # annotations) is read from the caller.
    "r152_definition_time_reads",
    "r154_bare_annotation_reads_the_caller",
    # Only the code around the objects that escape is extracted (r156); what
    # the block only calls or consumes moves with it (r157).
    "r156_created_objects_that_escape",
    "r157_created_objects_only_called",
    # The annotations' typing names are reached through a private alias of
    # typing, so no name the module binds or exports changes: not its own Any
    # (r160), not its public names under __all__ (r161), not the Callable a
    # star import bound (r162).
    "r160_host_binds_any_before_its_last_import",
    "r161_module_with_all_gains_no_public_name",
    "r162_host_star_imports_callable",
    # Round-3 audit: a memoized verdict answers only for its block's site. The
    # benign twins of a rebinding hazard, and the top-level twin of a copy in
    # a loop, still share helpers; the hazard and the loop copy
    # keep their code.
    "r7c_rebinding_enclosing_function_beside_a_benign_twin",
    "r7c_top_level_copy_beside_a_loop_copy",
    # The control for the mangled parameters and import names, which a class
    # body rewrites and a helper elsewhere would not.
    "r7c_unmangled_parameter_passed_by_keyword",
    # A block that starts after, or ends before, another statement on its
    # line: the call takes the block's place and the other statement stays.
    "r7sp_block_starts_after_a_semicolon",
    "r7sp_block_ends_before_a_semicolon",
    # A thunk evaluated after an effect is passed as a thunk, not eagerly:
    # a lambda's default, a set display's hashing, ``*`` unpacking, a read
    # of a global nothing binds.
    "r7sp_thunk_after_a_lambda_default",
    "r7sp_thunk_after_a_set_display",
    "r7sp_thunk_after_a_starred_display",
    "r7sp_thunk_after_an_unbound_global_read",
    # A thunk in dead code after a ``raise``, which the original never evaluated.
    "r7sp_thunk_after_a_raise",
    # Bindings audit of 1.772. A block that rebinds a name bound before it
    # (a for target, a capture, a def) is declined; the code around it moves.
    "r7bi_loop_capture_def_rebind_prebound",
    # The helper returns what a later += or del of it needs.
    "r7bi_augassign_and_del_after_block",
    # (r7bi_read_before_own_binding is declined: its block reads a local
    # before binding it, which only the original's UnboundLocalError shows.
    # r7bi_loop_del_before_eager_read is declined: a del later in the loop
    # leaves the name unbound on the next iteration.)
    # A binder that may be read unbound keeps its spelling: the deletion and
    # the handler stay with each site, and only what follows them moves.
    "r7bi_renamed_binder_named_by_unbound_error",
    # An except clause deletes its name as it ends: a try nested in an if
    # leaves nothing bound for the block to lose, and moves.
    "r7bi_except_name_in_nested_block",
    # The round-3 audit's P1 cases, ported from its families and its grammar
    # generator (r7fz_grammar_<its case id>_...), each now extracted soundly
    # by the fix of its defect. Each fixture's opening comment says what it
    # shows.
    "r7fz_binding_loop_var_leak",
    "r7fz_prebound_for_read_in_block",
    "r7fz_prebound_for_else_prebound",
    "r7fz_prebound_for_target_attr_prebound",
    "r7fz_prebound_match_capture_prebound",
    "r7fz_prebound_def_conditional_rebind",
    "r7fz_grammar_u0898_for_target_prebound",
    "r7fz_srctext_semicolons_continuations",
    "r7fz_thunks2_dict_unhashable",
    "r7fz_thunks2_dict_unpack",
    "r7fz_thunks2_lambda_default",
    "r7fz_thunks2_list_starred",
    "r7fz_thunks2_set_hash_effect",
    "r7fz_thunks2_set_unhashable",
    "r7fz_thunks2_tuple_starred",
    "r7fz_thunks2_undefined_global",
    "r7fz_misc_binder_renamed_del_message",
    "r7fz_grammar_t11254_read_before_bind",
    "r7fz_late_after_block_augassign",
    "r7fz_late_after_block_del",
    "r7fz_grammar_u0417_augassign_after_block",
    # The round-3 audit's families (r7fz_<family>_<case>): a sample of the
    # cases the audit found sound, one or more for each thing a family
    # targets, and every one transformed.
    "r7fz_binding_builtin_rebound_mid_module",
    "r7fz_binding_cell_filled_late",
    "r7fz_binding_cell_read_before_fill",
    "r7fz_binding_class_attr_vs_local",
    "r7fz_binding_dunder_name_file_samemodule",
    "r7fz_binding_global_rebound_by_callee",
    "r7fz_binding_module_getattr",
    "r7fz_binding_unbound_local_handler",
    "r7fz_builtins_differ_in_builtin",
    "r7fz_builtins_mock_patch_module",
    "r7fz_builtins_module_shadows_len",
    "r7fz_builtins_param_named_len",
    "r7fz_builtins_print_shadowed_local",
    "r7fz_classhost_dataclass_slots_super",
    "r7fz_classhost_dunder_class_same_class",
    "r7fz_classhost_enum_methods",
    "r7fz_classhost_getattr_fallback",
    "r7fz_classhost_private_same_class",
    "r7fz_classhost_siblings_plain",
    "r7fz_classhost_underscore_class_names",
    "r7fz_classhost_zero_arg_super",
    "r7fz_cluster_K_cell",
    "r7fz_cluster_generator_site",
    "r7fz_cluster_local_K",
    "r7fz_cluster_method_site",
    "r7fz_ctrl_break_in_try_finally",
    "r7fz_ctrl_match_class_pattern",
    "r7fz_ctrl_nested_try",
    "r7fz_ctrl_suppress_context",
    "r7fz_ctrl_walrus_while",
    "r7fz_directives_fmt_off_region",
    # Round 4: the statements fmt: off and fmt: skip keep move as written.
    "r9dr_fmt_off_layout_moves_verbatim",
    "r7fz_directives_noqa_on_argument",
    "r7fz_directives_pragma_branch",
    "r7fz_directives_type_ignore_line",
    "r7fz_extra_pragma_with_header",
    "r7fz_literals_cast_literal",
    "r7fz_literals_enum_functional",
    "r7fz_literals_gettext_differ",
    "r7fz_literals_typevar_name",
    "r7fz_misc_async_no_await",
    "r7fz_misc_binder_renamed_unbound_message_inner",
    "r7fz_misc_block_raises_in_handler_context",
    "r7fz_misc_decorated_lru_cache",
    "r7fz_misc_except_star_reraise",
    "r7fz_misc_generator_return_block",
    "r7fz_misc_mutable_default_host",
    "r7fz_misc_nested_host_called_early",
    "r7fz_misc_super_inside_block",
    "r7fz_prebound_class_conditional_rebind",
    "r7fz_prebound_for_tuple_target",
    "r7fz_prebound_try_except_else_bind",
    "r7fz_prebound_while_walrus_prebound",
    "r7fz_prebound_with_enter_raises_caught",
    "r7fz_srctext_bom",
    "r7fz_srctext_cp1252_quotes",
    "r7fz_srctext_crlf",
    "r7fz_srctext_form_feed",
    "r7fz_srctext_fstring_nested_quotes",
    "r7fz_srctext_latin1_literals",
    "r7fz_srctext_named_escape",
    "r7fz_srctext_tabs_indent",
    "r7fz_thunks2_lambda_kwdefault",
    "r7fz_thunks2_plain_first",
    "r7fz_thunks2_property_first",
    "r7fz_thunks2_two_thunks_order",
    "r7fz_thunks_call_arg_order",
    "r7fz_thunks_conditional",
    "r7fz_thunks_dict_order",
    "r7fz_thunks_lambda_default_first",
    "r7fz_thunks_match_guard",
    "r7fz_thunks_short_circuit_and",
    # A decorator the project defines that only wraps or registers the
    # function leaves its body alone; the r7d_instrumenting_* fixtures, whose
    # decorators recompile the body, are declined.
    "r7d_plain_wrapper_decorator",
    # The same applied by hand, with a known factory and a property built from
    # its accessors; r7d_instrumenting_call_applied_by_hand, whose recompiler is
    # applied by a call, and r7d_metaclass_recompiles_methods are declined.
    "r7d_plain_wrapper_applied_by_hand",
    # A TestCase, read to be built by type, takes the class-private helper, which
    # neither unittest nor pytest collects as a test.
    "r7d_method_helper_in_a_testcase",
}
# r7fz_classhost_init_subclass_wraps and r7fz_classhost_metaclass_registry,
# which the round-3 audit found extracted soundly, are declined since code
# stopped moving out of classes whose machinery the method-host test cannot
# read: an __init_subclass__ that wraps methods, and a project metaclass.
# r7sp_directive_on_a_shared_line is declined: each block starts after, or
# ends before, a statement that stays on a line carrying a directive.
# r7fz_grammar_u0217_read_before_bind, a P1-7 case, is declined since its fix:
# its block reads v6 before binding it (incomplete_lifetime_block1).
# p09, p13, p14 and p17 left it when the classes code moves out of began to be
# held to the method-host test of their machinery: p09's metaclass is the
# project's, p13 and p14 derive from Protocol, whose machinery the test does
# not accept, and p17's base carries a decorator not known to keep it.
# p15_class_decorator_rebuilds_namespace and p16_class_decorator_wraps_every_function
# left the set when a class decorator not known to leave its methods alone began
# to decline blocks in them: the one rebuilds the class from its namespace, the
# other wraps every method, and either might as well have recompiled them.
# r153_class_definition_reads left the set when a class defined in the block
# began to decline it: every instance and the class itself show the helper in
# their qualified names. Its reads are still what free_variables reports.
# r85_conditionally_bound_parameter left the set when a thunk of a local that
# may be unbound at the call began to be declined: the thunk would raise
# NameError where the block raised UnboundLocalError. Its own read never
# happens unbound, but nothing short of path correlation can show that.
# Every other fixture must come back byte-identical. One of them once changed:
# r86_annotated_assignment_live left the set when the trivial-helper filter
# began declining its shared block, which binds only a literal and a parameter.

KNOWN_DEFECTS: Dict[str, str] = {}
"""Fixtures whose defect is reported and not yet fixed, each with its reason from
``tests/audit_defects.py``. None is open: every round-3 P1 fixture passes."""


def _run(script: Path) -> tuple[int, str, list[str]]:
    return observe(script.name, script.parent)


@pytest.mark.parametrize(
    "case", with_known_defects(sorted(path.stem for path in CASES.glob("*.py")), KNOWN_DEFECTS)
)
def test_refactoring_preserves_program_output(case: str) -> None:
    parsed_or_skipped(CASES / f"{case}.py")
    with tempfile.TemporaryDirectory(prefix="towel-hostile-") as directory:
        root = Path(directory)
        before = root / "before" / "m.py"
        after = root / "after" / "m.py"
        before.parent.mkdir()
        after.parent.mkdir()
        shutil.copy(CASES / f"{case}.py", before)
        shutil.copy(CASES / f"{case}.py", after)
        applied = refactor_script(after)
        transformed = before.read_bytes() != after.read_bytes()
        assert transformed == (applied > 0)
        assert _run(after) == _run(before)
        if transformed:
            # No public name of the module appears, disappears, or changes meaning.
            assert module_faces(after.parent, ["m"]) == module_faces(before.parent, ["m"])
        if case not in KNOWN_DEFECTS:
            assert transformed == (case in TRANSFORMED), (
                "rejected" if not transformed else "transformed"
            )
