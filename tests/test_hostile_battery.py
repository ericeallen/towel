"""Execute hostile fixtures before and after fixed-point refactoring.

Each fixture in ``tests/hostile_cases`` is a script whose ``__main__`` block
prints every observation that an extraction could disturb: evaluation order,
evaluation count, conditional evaluation, closure cells, deletion, and
pattern bindings. The battery asserts that the program's output is identical
after refactoring, and records per fixture whether the current engine
transforms it or rejects it, so a change in either direction is visible.

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

import pytest

from tests.audit_defects import (
    P1_1_PREBOUND_REBINDING,
    P1_2_SEMICOLON_LINE,
    P1_3_LEADING_THUNK,
    P1_5_RENAMED_BINDER,
    P1_7_READ_BEFORE_BIND,
    P1_8_LATER_UPDATE,
)
from tests.hostile_execution import observe, parsed_or_skipped
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
    "p09_static_helper_metaclass_hides_attribute",
    # Classes that cannot take a helper into their body: it goes to module level.
    "p10_one_line_exception_base",
    "p11_one_line_base_docstring_and_assignment",
    "p12_one_line_body_after_a_split_header",
    "p13_protocol_default_methods",
    "p14_protocol_common_ancestor",
    "p15_class_decorator_rebuilds_namespace",
    "p16_class_decorator_wraps_every_function",
    "p17_decorated_base_rebuilds_namespace",
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
}
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

KNOWN_DEFECTS = {
    # The round-3 audit's P1 cases, ported from its families and its grammar
    # generator (r7fz_grammar_<its case id>_...). Each fixture's opening comment
    # says what it shows.
    "r7fz_binding_loop_var_leak": P1_1_PREBOUND_REBINDING,
    "r7fz_prebound_for_read_in_block": P1_1_PREBOUND_REBINDING,
    "r7fz_prebound_for_else_prebound": P1_1_PREBOUND_REBINDING,
    "r7fz_prebound_for_target_attr_prebound": P1_1_PREBOUND_REBINDING,
    "r7fz_prebound_match_capture_prebound": P1_1_PREBOUND_REBINDING,
    "r7fz_prebound_def_conditional_rebind": P1_1_PREBOUND_REBINDING,
    "r7fz_grammar_u0898_for_target_prebound": P1_1_PREBOUND_REBINDING,
    "r7fz_srctext_semicolons_continuations": P1_2_SEMICOLON_LINE,
    "r7fz_thunks2_dict_unhashable": P1_3_LEADING_THUNK,
    "r7fz_thunks2_dict_unpack": P1_3_LEADING_THUNK,
    "r7fz_thunks2_lambda_default": P1_3_LEADING_THUNK,
    "r7fz_thunks2_list_starred": P1_3_LEADING_THUNK,
    "r7fz_thunks2_set_hash_effect": P1_3_LEADING_THUNK,
    "r7fz_thunks2_set_unhashable": P1_3_LEADING_THUNK,
    "r7fz_thunks2_tuple_starred": P1_3_LEADING_THUNK,
    "r7fz_thunks2_undefined_global": P1_3_LEADING_THUNK,
    "r7fz_misc_binder_renamed_del_message": P1_5_RENAMED_BINDER,
    "r7fz_grammar_u0217_read_before_bind": P1_7_READ_BEFORE_BIND,
    "r7fz_grammar_t11254_read_before_bind": P1_7_READ_BEFORE_BIND,
    "r7fz_late_after_block_augassign": P1_8_LATER_UPDATE,
    "r7fz_late_after_block_del": P1_8_LATER_UPDATE,
    "r7fz_grammar_u0417_augassign_after_block": P1_8_LATER_UPDATE,
}


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
        if case not in KNOWN_DEFECTS:
            assert transformed == (case in TRANSFORMED), (
                "rejected" if not transformed else "transformed"
            )
