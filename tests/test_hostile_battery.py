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
direction is visible. The lifetime-refusal profile preserves the same semantic
assertions while requiring an explicit refusal on uncertified interpreters.

A fixture in ``KNOWN_DEFECTS`` shows a defect an audit reported and is not
yet fixed: it is expected to fail, strictly, and its transformed state is
not pinned, since the fix may extract it soundly or decline it. When the fix
lands the fixture passes, pytest reports the XPASS as a failure, and the
fixture moves from ``KNOWN_DEFECTS`` to ``TRANSFORMED`` or stays out of both.

``REFLECTION_CASES`` instead records examples outside the equivalence contract.
Their separate test pins extraction or an ordinary ownership refusal and
valid syntax, without asserting preservation for reflection or instrumentation.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import shutil
import tempfile
from types import CodeType
from typing import Dict

import pytest

from tests.compiler_golden_profiles import lifetime_refusal_profile
from tests.hostile_execution import ScopeWatch, fixture_named, fixture_sources, module_faces
from tests.hostile_execution import observe, parsed_or_skipped
from tests.hostile_refactoring import OWNERSHIP_BOOKKEEPING, refactor_script
from tests.hostile_refactoring import with_known_defects
from towel.unification.argument_ownership import _supported_runtime
from towel.unification.refactor_engine import UnificationRefactorEngine

# The battery's pins are 1.949's: which fixtures strict mode transforms, and
# which its ownership proof refuses. Relaxed mode, the default, has its own
# pass below (test_relaxed_refactoring_preserves_program_output).
pytestmark = pytest.mark.strict_mode

CASES = Path(__file__).parent / "hostile_cases"

HISTORICAL_EXTRACTIONS = {
    "r1792_factory_finalizer_continuation",
    "r1792_factory_finalizer_order",
    "r1792_aliased_suppress_conditional_read",
    # Returning opaque call results keeps these names local at the callers,
    # including their earlier unbound reads (the battery checks both).
    "r9bd_only_binding_read_before_block",
    "rmypyoverlay_constructor_fields",
    # The consumer-formatting regression also retains its original results
    # and scopes under the ordinary hostile battery.
    "r1792_formatting_destination_indentation",
    # September 27: mixed expression list children can be passed as thunks;
    # nxt() and nxt() * 10 are still called once per iteration, in order.
    "h03_loop_reeval",
    # The complete inner body, including both del X and its later read, moves
    # together. X stays local and unbound in the helper; no read is left in
    # inner to resolve to the enclosing X. ScopeWatch and execution both check
    # this boundary, so a future partial move must not be accepted here.
    "r9bd_only_binding_deleted_read_after",
    "p09_static_helper_metaclass_hides_attribute",
    "p13_protocol_default_methods",
    "p14_protocol_common_ancestor",
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
    # Lazy module callee lookup remains inside the conditional branch.
    "r41_conditional_call_param",
    "r44_cluster_nested_overlap",
    "r45_async_no_await",
    "r46_nonlocal_counter",
    "r47_lru_cache_method",
    "r54_with_binding_used_after",
    "r55_chained_assign",
    "h10_short_circuit",
    "r66_conditional_callee",
    "r60_local_import",
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
    # Closed factories may escape in relaxed mode (D4, r156); captured objects retain their
    # checks. What the block only calls or consumes moves with it (r157).
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
    # r7bi_read_before_own_binding is declined: its block reads a local
    # before binding it, which only the original's UnboundLocalError shows.
    # r7bi_loop_del_before_eager_read can move its complete body: the loop's
    # read and later del remain together, including the next unbound read.
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
    # r7fz_misc_decorated_lru_cache is declined: keeping the loop's call-
    # supplied item alive requires returning a target an empty loop never binds.
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
    # Ordinary wrappers and registries retain their call behavior.
    "r7d_plain_wrapper_decorator",
    # The same applied by hand, with a factory and a property built from its
    # accessors. Body recompilation is covered separately by REFLECTION_CASES.
    "r7d_plain_wrapper_applied_by_hand",
    # A TestCase, read to be built by type, takes the class-private helper, which
    # neither unittest nor pytest collects as a test.
    "r7d_method_helper_in_a_testcase",
    # Round-4 P1-01 to P1-03: the parameter is substituted in a format
    # specification and in the loads of a binding target, and not inside a
    # lambda whose parameter is spelled like it.
    "r9sb_fstring_format_spec",
    "r9sb_binding_target_loads",
    "r9sb_lambda_parameter_capture",
    # Round-4 audit, P1-04: a block holding its function's only binding of a
    # name the function reads elsewhere is declined, since the name would stop
    # being local (r9bd_only_binding_read_before_block, the audit's reproducer;
    # r9bd_only_binding_deleted_read_after; r9bd_type_alias_only_binding). In
    # these, only the code after each binding moves, and the binding stays.
    "r9bd_only_binding_every_binder",
    "r9bd_only_binding_read_by_nested_scopes",
    # The controls: the call assigns the name back, and a comprehension's own
    # target of the same spelling reads nothing of the function's.
    "r9bd_only_binding_kept_local",
    # A helper declares global every name its sites' functions declare and
    # the block binds, by any construct; sites whose functions declare
    # differently share no helper for code touching the name, and only what
    # follows the binding moves.
    "r9bd_global_bound_by_every_binder",
    "r9bd_global_declared_at_one_site",
}
# r7sp_directive_on_a_shared_line is declined: each block starts after, or
# ends before, a statement that stays on a line carrying a directive.
# r7fz_grammar_u0217_read_before_bind, a P1-7 case, is declined since its fix:
# its block reads v6 before binding it (incomplete_lifetime_block1).
# p09, p13 and p14 transform without the implicit-machinery guard.
# Static helpers and helpers extracted from a Protocol remain at module level;
# an ancestor's decorator does not decorate the subclass's own methods.
# r153_class_definition_reads left the set when a class defined in the block
# began to decline it. Escaping classes remain conservative because their
# methods can close over bindings outside the block.
# r85_conditionally_bound_parameter left the set when a thunk of a local that
# may be unbound at the call began to be declined: the thunk would raise
# NameError where the block raised UnboundLocalError. Its own read never
# happens unbound, but nothing short of path correlation can show that.
# Every other fixture must come back byte-identical. One of them once changed:
# r86_annotated_assignment_live left the set when the trivial-helper filter
# began declining its shared block, which binds only a literal and a parameter.

# These original fixtures continue to check ordinary effects and scope. Their
# earlier extraction pins predate the ownership proof: an exceptional exit
# cannot hand new owned locals back to a caller retaining other values, and
# a conditionally unbound local cannot be returned just to extend its lifetime.
# Audit D1 recovers whole-body r108/r69 extractions; D3 recovers u0417
# renamed locals. D4 recovers r156 only in relaxed mode; strict retains the
# created-object and partial-frame refusals. Runtime, scope and public-name checks remain intact.
OWNERSHIP_REFUSALS: Dict[str, str] = {
    "r156_created_objects_that_escape": "strict mode keeps created-object and partial-frame refusals",
    "h04_closure_freevar": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "h61_str_method_param": "retained local is not definitely bound on every block exit",
    "r103_lambda_parameter_spelling": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r107_unbound_global_argument": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r109_eager_optional_import_failed": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r110_eager_type_checking_import": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r111_eager_module_except_as_name": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r112_eager_module_match_capture_unmatched": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r113_eager_class_attr_in_method": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r114_eager_nested_class_attr": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r115_eager_class_in_function_attr": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r116_eager_enclosing_bound_after_inner_call": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r117_eager_enclosing_deleted_before_inner_call": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r118_eager_enclosing_conditionally_bound": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r119_eager_module_def_and_class_after_call": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r120_eager_module_import_after_call": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r121_eager_comprehension_variable": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r122_eager_lambda_parameter": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r130_resource_observed_after_block_differs": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r133_formfeed": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r139_literal_roundtrip": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r140_unbound_name_on_untaken_branch": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r141_module_name_bound_after_an_early_call": "retained local is not definitely bound on every block exit",
    "r142_module_data_rebound_by_callback": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r143_module_name_shadowed_by_a_local_elsewhere": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r154_bare_annotation_reads_the_caller": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r157_created_objects_only_called": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r1792_aliased_suppress_conditional_read": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r1792_factory_finalizer_continuation": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r1792_factory_finalizer_order": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r39_cluster_alpha": "ordered retained-value slots do not align across sites",
    "r45_async_no_await": "retained local is not definitely bound on every block exit",
    "r46_nonlocal_counter": "retained local is not definitely bound on every block exit",
    "r76_return_order": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7bi_augassign_and_del_after_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7bi_except_name_in_nested_block": "retained local is not definitely bound on every block exit",
    "r7bi_renamed_binder_named_by_unbound_error": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7c_top_level_copy_beside_a_loop_copy": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7d_method_helper_in_a_testcase": "retained local is not definitely bound on every block exit",
    "r7fz_binding_builtin_rebound_mid_module": "retained local is not definitely bound on every block exit",
    "r7fz_binding_class_attr_vs_local": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_builtins_differ_in_builtin": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_builtins_param_named_len": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_builtins_print_shadowed_local": "retained local is not definitely bound on every block exit",
    "r7fz_classhost_dataclass_slots_super": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_dunder_class_same_class": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_enum_methods": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_getattr_fallback": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_siblings_plain": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_underscore_class_names": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_zero_arg_super": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_directives_fmt_off_region": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_late_after_block_augassign": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_late_after_block_del": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_literals_cast_literal": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_literals_enum_functional": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_literals_gettext_differ": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_literals_typevar_name": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_misc_async_no_await": "retained local is not definitely bound on every block exit",
    "r7fz_misc_binder_renamed_del_message": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_misc_binder_renamed_unbound_message_inner": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_misc_generator_return_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_misc_mutable_default_host": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_misc_nested_host_called_early": "retained local is not definitely bound on every block exit",
    "r7fz_misc_super_inside_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_prebound_class_conditional_rebind": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_prebound_def_conditional_rebind": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_thunks2_lambda_kwdefault": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_thunks_conditional": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_thunks_lambda_default_first": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_thunks_short_circuit_and": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7sp_block_ends_before_a_semicolon": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r87_nested_function_in_method": "physical helper parameters plus ownership holder exceed max5",
    "r94_import_binds_live_name": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r96_unpacked_targets_live": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r9bd_only_binding_read_before_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
}
REFLECTION_OWNERSHIP_REFUSALS: Dict[str, str] = {
    "r106_dir_reads_the_frame": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r123_direct_eval_after_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r124_eval_alias_reads_preblock_name": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r125_locals_dir_after_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r127_eval_alias_partial_block": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r145_getframe_assigned_alias": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_init_subclass_wraps": "owned bindings straddle a partial or uncertified whole-body frame boundary",
    "r7fz_classhost_metaclass_registry": "owned bindings straddle a partial or uncertified whole-body frame boundary",
}
# r162 retains its original behavior check, but its wildcard import and bare
# print reads now require conservative builtin-mapping refusal.
# D4 keeps r9sb closed callback signatures unchanged: the unifier lacks the
# module/function context to prove those differently named callbacks cannot
# escape. A matching-signature variant retains positive capture coverage.
TRANSFORMED = (
    HISTORICAL_EXTRACTIONS
    - OWNERSHIP_REFUSALS.keys()
    - {"r162_host_star_imports_callable", "r9sb_lambda_parameter_capture"}
) | {
    # Occurrence-specific constants recover complete-body ownership handoffs.
    # Conditional bindings and loop deletion move with their unbound reads;
    # no eager argument or partial helper changes the exception or lifetime.
    "p19_thunk_of_possibly_unbound_local",
    "p20_thunk_of_loop_variable",
    "r7bi_loop_del_before_eager_read",
    # The recovered historical deletion, directive, source-text and match
    # cases likewise no longer straddle a partial ownership boundary.
    # Certified complete-body handoff, or a binding-free inner effect block.
    "r1792_protocol_context_target",
    "r1792_protocol_iteration_target",
    "r1792_value_return_owner",
    # Gemini review of 2026-10-31: the statement after a semicolon on the
    # block's last line reads c, so the helper returns it.
    "rgm_orphan_semicolon_after_block_in_same_named_methods",
    # Twins that differ in an expression over their loop variable
    # (``total += i`` against ``total += i * 2``) pass it as a function of
    # that variable since 2.0, so their whole bodies unify and move under a
    # certified handoff. Through 1.949 only partial blocks of them unified:
    # h39 and h52 were ownership refusals, and these three found nothing.
    "h51_semicolons",
    "h53_nested_func_reads_retvar",
    "r79_list_display_in_loop",
}

# Reviewed Python 3.14/3.15 refusal profile: these fixtures previously used
# a frame-order proof. Keep their behavioral assertions and certify the reason
# explicitly; no known defect or lifetime assertion becomes a skip.
UNCERTIFIED_OWNERSHIP_REFUSALS = frozenset(
    {
        "h01_side_effect_order",
        "h03_loop_reeval",
        "h10_short_circuit",
        "h13_dunder_file",
        "h26_match_capture",
        "p01_static_helper_class_name_shadowed_by_parameter",
        "p02_static_helper_class_deleted",
        "p03_static_helper_mangled_class_name",
        "p04_static_helper_class_decorator_returns_factory",
        "p05_static_helper_class_global_rebound",
        "p06_static_helper_called_while_class_body_runs",
        "p07_static_methods_called_while_class_body_runs",
        "p08_classmethod_never_using_cls",
        "p09_static_helper_metaclass_hides_attribute",
        "p10_one_line_exception_base",
        "p11_one_line_base_docstring_and_assignment",
        "p12_one_line_body_after_a_split_header",
        "p13_protocol_default_methods",
        "p14_protocol_common_ancestor",
        "p18_known_class_decorators_keep_the_helper",
        "p19_thunk_of_possibly_unbound_local",
        "p20_thunk_of_loop_variable",
        "p21_helper_placed_after_an_assignment_that_calls_it",
        "r101_same_named_method_forces_module_helper",
        "r102_whole_body_reuse",
        "r104_walrus_target_spelling",
        "r131_weakref_and_id_identity",
        "r147_wide_int_literal",
        "r152_definition_time_reads",
        "r158_getattr_serves_unknown_names_from_data",
        "r160_host_binds_any_before_its_last_import",
        "r161_module_with_all_gains_no_public_name",
        "r1792_formatting_destination_indentation",
        "r1792_value_return_owner",
        "r27_del_subscript",
        "r47_lru_cache_method",
        "r50_helper_name_collision",
        "r54_with_binding_used_after",
        "r55_chained_assign",
        "r57_star_unpack",
        "r60_local_import",
        "r63_except_var_used_in_handler",
        "r7bi_loop_capture_def_rebind_prebound",
        "r7bi_loop_del_before_eager_read",
        "r7c_unmangled_parameter_passed_by_keyword",
        "r7d_plain_wrapper_applied_by_hand",
        "r7d_plain_wrapper_decorator",
        "r7fz_binding_dunder_name_file_samemodule",
        "r7fz_binding_global_rebound_by_callee",
        "r7fz_binding_loop_var_leak",
        "r7fz_binding_module_getattr",
        "r7fz_binding_unbound_local_handler",
        "r7fz_builtins_mock_patch_module",
        "r7fz_builtins_module_shadows_len",
        "r7fz_cluster_K_cell",
        "r7fz_cluster_generator_site",
        "r7fz_cluster_local_K",
        "r7fz_cluster_method_site",
        "r7fz_ctrl_break_in_try_finally",
        "r7fz_ctrl_match_class_pattern",
        "r7fz_ctrl_nested_try",
        "r7fz_ctrl_suppress_context",
        "r7fz_ctrl_walrus_while",
        "r7fz_directives_noqa_on_argument",
        "r7fz_directives_pragma_branch",
        "r7fz_directives_type_ignore_line",
        "r7fz_extra_pragma_with_header",
        "r7fz_prebound_for_else_prebound",
        "r7fz_prebound_for_read_in_block",
        "r7fz_prebound_for_tuple_target",
        "r7fz_prebound_match_capture_prebound",
        "r7fz_prebound_try_except_else_bind",
        "r7fz_prebound_while_walrus_prebound",
        "r7fz_srctext_bom",
        "r7fz_srctext_cp1252_quotes",
        "r7fz_srctext_crlf",
        "r7fz_srctext_form_feed",
        "r7fz_srctext_fstring_nested_quotes",
        "r7fz_srctext_latin1_literals",
        "r7fz_srctext_named_escape",
        "r7fz_srctext_semicolons_continuations",
        "r7fz_srctext_tabs_indent",
        "r7fz_thunks2_dict_unhashable",
        "r7fz_thunks2_dict_unpack",
        "r7fz_thunks2_lambda_default",
        "r7fz_thunks2_list_starred",
        "r7fz_thunks2_plain_first",
        "r7fz_thunks2_property_first",
        "r7fz_thunks2_set_hash_effect",
        "r7fz_thunks2_set_unhashable",
        "r7fz_thunks2_tuple_starred",
        "r7fz_thunks2_two_thunks_order",
        "r7fz_thunks2_undefined_global",
        "r7fz_thunks_call_arg_order",
        "r7fz_thunks_dict_order",
        "r7fz_thunks_match_guard",
        "r7sp_block_starts_after_a_semicolon",
        "r7sp_thunk_after_a_lambda_default",
        "r7sp_thunk_after_a_raise",
        "r7sp_thunk_after_a_set_display",
        "r7sp_thunk_after_a_starred_display",
        "r7sp_thunk_after_an_unbound_global_read",
        "r80_inlined_leading_thunk",
        "r81_conditionally_bound_free_variable",
        "r83_tab_indented_class",
        "r90_cluster_across_classes",
        "r9dr_fmt_off_layout_moves_verbatim",
        "r9sb_fstring_format_spec",
        "rmypyoverlay_constructor_fields",
        # The unify lane's matching of renamed locals made these four strict
        # extractions, each through a certified ownership transfer, which an
        # uncertified interpreter declines like the rest. (r9sb's lambda
        # spelling, in turn, no longer unifies in either mode, so no lifetime
        # refusal is left to observe there.)
        "r108_clustered_sites_assign_the_returned_name",
        "r156_created_objects_that_escape",
        "r69_annassign",
        "r7fz_grammar_u0417_augassign_after_block",
    }
)
UNCERTIFIED_REFLECTION_REFUSALS = frozenset(
    {
        "p15_class_decorator_rebuilds_namespace",
        "p16_class_decorator_wraps_every_function",
        "p17_decorated_base_rebuilds_namespace",
        "r159_frame_handle_before_a_nested_block",
        "r7d_instrumenting_call_applied_by_hand",
        "r7d_instrumenting_class_decorator",
        "r7d_instrumenting_enclosing_function",
        "r7d_instrumenting_function_decorator",
        "r7d_metaclass_recompiles_methods",
        "r84_warn_stacklevel",
        "r9dc_stacked_decoration_by_hand",
    }
)

if lifetime_refusal_profile():
    OWNERSHIP_REFUSALS |= dict.fromkeys(
        UNCERTIFIED_OWNERSHIP_REFUSALS, "lifetime_order_uncertified"
    )
    REFLECTION_OWNERSHIP_REFUSALS |= dict.fromkeys(
        UNCERTIFIED_REFLECTION_REFUSALS, "lifetime_order_uncertified"
    )
    TRANSFORMED -= UNCERTIFIED_OWNERSHIP_REFUSALS

KNOWN_DEFECTS: Dict[str, str] = {}
"""Fixtures whose defect is reported and not yet fixed, each with its reason from
``tests/audit_defects.py``. None is open: every round-3 P1 fixture passes."""

REFLECTION_CASES: Dict[str, str] = {
    "p15_class_decorator_rebuilds_namespace": "a decorator drops helpers from a rebuilt namespace",
    "p16_class_decorator_wraps_every_function": "a decorator wraps scanned class members",
    "p17_decorated_base_rebuilds_namespace": "a decorator rebuilds an ancestor's namespace",
    "r84_warn_stacklevel": "warning locations depend on the call stack",
    "r106_dir_reads_the_frame": "dir() observes the local namespace",
    "r123_direct_eval_after_block": "eval() reads the local namespace",
    "r124_eval_alias_reads_preblock_name": "an eval alias reads the local namespace",
    "r125_locals_dir_after_block": "locals() and dir() observe moved bindings",
    "r126_locals_in_nested_scope": "nested functions inspect local namespaces",
    "r127_eval_alias_partial_block": "an eval alias observes moved bindings",
    "r128_warn_stacklevel_via_alias": "warning aliases observe the call stack",
    "r129_warning_registry_dedup": "warning deduplication depends on source locations",
    "r145_getframe_assigned_alias": "an aliased frame reader observes moved bindings",
    "r146_module_function_reads_caller_frame": "a function inspects its caller's frame",
    "r159_frame_handle_before_a_nested_block": "a retained frame observes moved bindings",
    "r7d_instrumenting_call_applied_by_hand": "a call recompiles function bodies",
    "r7d_instrumenting_class_decorator": "a class decorator recompiles methods",
    "r7d_instrumenting_enclosing_function": "a decorator recompiles enclosing function bodies",
    "r7d_instrumenting_function_decorator": "a decorator recompiles function bodies",
    "r7d_metaclass_recompiles_methods": "a metaclass recompiles method bodies",
    "r7fz_classhost_init_subclass_wraps": "a subclass hook wraps names found by scanning vars(cls)",
    "r7fz_classhost_metaclass_registry": "a metaclass publishes the scanned class namespace",
    "r9dc_stacked_decoration_by_hand": "stacked calls recompile function bodies",
}


def _r102_transforms_on_this_compiler(fixture: Path) -> bool:
    """Only this fixture's comprehension parameter cells vary across compilers."""
    parameters = {
        "scale": ("items", "factor"),
        "rescale": ("values", "k"),
        "summarize": ("data", "weight", "label"),
        "render": ("self", "data", "weight"),
    }
    pending = [compile(fixture.read_text(), str(fixture), "exec", dont_inherit=True)]
    functions: list[CodeType] = []
    while pending:
        code = pending.pop()
        pending.extend(value for value in code.co_consts if isinstance(value, CodeType))
        if code.co_name in parameters:
            functions.append(code)
    assert len(functions) == 4 and {code.co_name for code in functions} == set(parameters)
    captures = []
    for code in functions:
        actual_parameters = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
        assert actual_parameters == parameters[code.co_name]
        captures.append(bool(set(actual_parameters).intersection(code.co_cellvars)))
    assert all(captures) or not any(captures), "Unknown mixed r102 parameter-cell facts"
    return not any(captures) and not lifetime_refusal_profile()


def _assert_lifetime_order_refusal(script: Path) -> None:
    engine = UnificationRefactorEngine(min_lines=3, skip_trivial_helpers=False)
    engine.analyze_file(str(script))
    assert engine.declined_pairs.get("lifetime_order_uncertified", 0) > 0, engine.declined_pairs


def _run(script: Path) -> tuple[int, str, list[str]]:
    return observe(script.name, script.parent)


@pytest.mark.parametrize(
    "case",
    with_known_defects(
        [path.stem for path in fixture_sources(CASES) if path.stem not in REFLECTION_CASES],
        KNOWN_DEFECTS,
    ),
)
def test_refactoring_preserves_program_output(case: str) -> None:
    fixture = fixture_named(CASES, case)
    parsed_or_skipped(fixture)
    with tempfile.TemporaryDirectory(prefix="towel-hostile-") as directory:
        root = Path(directory)
        before = root / "before" / "m.py"
        after = root / "after" / "m.py"
        before.parent.mkdir()
        after.parent.mkdir()
        shutil.copy(fixture, before)
        shutil.copy(fixture, after)
        scopes = ScopeWatch()
        # This historical control deliberately pins the default trivial-helper refusal.
        applied = refactor_script(
            after,
            file_finisher=scopes,
            skip_trivial_helpers=case == "r86_annotated_assignment_live",
        )
        transformed = before.read_bytes() != after.read_bytes()
        assert transformed == (applied > 0)
        # No change Towel rendered moves a name a kept function reads to
        # another scope: every local a function loses goes into the helper
        # with each read of it.
        assert scopes.found == []
        assert _run(after) == _run(before)
        if transformed:
            # No public name of the module appears, disappears, or changes meaning.
            assert module_faces(after.parent, ["m"]) == module_faces(before.parent, ["m"])
        if lifetime_refusal_profile() and case in UNCERTIFIED_OWNERSHIP_REFUSALS:
            _assert_lifetime_order_refusal(after)
        if case not in KNOWN_DEFECTS:
            expected = (
                _r102_transforms_on_this_compiler(fixture)
                if case == "r102_whole_body_reuse"
                else case in TRANSFORMED
            )
            assert transformed == expected, "rejected" if not transformed else "transformed"


_EARLY = "a __del__ records when a local nothing reads again is released: at the helper's return"
_ORDER = "a __del__ records the order in which one function exit releases an argument and a local"
RELAXED_OBSERVES_FINALIZATION: Dict[str, str] = {
    "r1792_external_exception_partial": "two locals are finalized after a caught exception",
    "r1792_factory_finalizer_continuation": _EARLY,
    "r1792_factory_finalizer_order": _EARLY,
    "r1792_protocol_caught_exception": _EARLY,
    "r1792_protocol_context_target": _EARLY,
    "r1792_protocol_explicit_call_control": _EARLY,
    "r1792_protocol_iteration_target": _EARLY,
    "r1792_protocol_nested_if": _EARLY,
    "r1792_protocol_operator": _EARLY,
    "r1792_protocol_property": _EARLY,
    "r1792_protocol_subscript": _EARLY,
    "r1792_value_return_owner": _ORDER,
}
"""Fixtures whose output differs in relaxed mode, each because it prints when an object is finalized.

Relaxed mode assumes a program does not depend on when or in what order
objects are finalized once it has stopped using them (docs/design/DECISIONS.md,
"Relaxed mode is the default; strict mode keeps exact lifetimes"). These
fixtures were written to observe exactly that, so relaxed mode may reorder
their finalizer lines; each entry says what the fixture watches. Any other
difference in relaxed mode is a defect to fix, not an entry for this list,
and an entry whose output stops differing fails until it is removed.
"""


_PROTOCOL_FINALIZERS = frozenset({"drop:first", "drop:second"})
_RESOURCE_FINALIZERS = frozenset({"inner:first", "outer:first", "inner:second", "outer:second"})
_FACTORY_FINALIZERS = {
    "r1792_factory_finalizer_continuation": frozenset({"closed"}),
    "r1792_factory_finalizer_order": frozenset({"z", "a"}),
}
Event = int | str


def _event_sequence(expression: str) -> tuple[Event, ...]:
    parsed: object = ast.literal_eval(expression)
    assert isinstance(parsed, (list, tuple)), "fixture must print an event sequence"
    assert all(type(event) in (int, str) for event in parsed), "unexpected event type"
    return tuple(parsed)


def _split_events(output: str) -> tuple[str, tuple[Event, ...]]:
    prefix, separator, events = output.rpartition("[")
    assert separator and output.endswith("\n"), "fixture must end with its event list"
    return prefix, _event_sequence(separator + events)


def _events(
    output: str, finalizers: frozenset[str] = _PROTOCOL_FINALIZERS
) -> tuple[str, tuple[Event, ...], tuple[str, ...]]:
    """Preserve printed values and ordinary effect order; only finalizers may move."""
    prefix, events = _split_events(output)
    return (
        prefix,
        tuple(event for event in events if event not in finalizers),
        tuple(sorted(event for event in events if isinstance(event, str) and event in finalizers)),
    )


def _factory_events(
    output: str, finalizers: frozenset[str]
) -> tuple[tuple[Event, ...], tuple[str, ...]]:
    """Validate the two factories' finalizer-dependent counts and event snapshots.

    Each call emits one ordinary number and owns one object per finalizer label.
    The first return counts its number plus zero or more of its finalizers.
    The second return snapshots both numbers, all first-call finalizers and
    zero or more second-call finalizers. Both calls have finished by print.
    """
    prefix, events = _split_events(output)
    matched = re.fullmatch(r"(\d+) (\(.*\)) ", prefix)
    assert matched is not None, "factory must print its count and tuple snapshot"
    count = int(matched.group(1))
    snapshot = _event_sequence(matched.group(2))
    ordinary = tuple(event for event in events if event not in finalizers)
    assert len(ordinary) == 2 and all(type(event) is int for event in ordinary)
    assert 1 <= count <= 1 + len(finalizers), "first count changed beyond finalization"
    first_snapshot = events[:count]
    assert tuple(event for event in first_snapshot if event not in finalizers) == ordinary[:1]
    assert all(first_snapshot.count(event) <= 1 for event in finalizers)
    assert tuple(event for event in snapshot if event not in finalizers) == ordinary
    assert events[: len(snapshot)] == snapshot, "snapshot is not a prefix of the final trace"
    for event in finalizers:
        assert events.count(event) == 2, "a factory finalizer was lost or duplicated"
        assert 1 <= snapshot.count(event) <= 2, "snapshot lost a first-call finalizer"
    return ordinary, tuple(sorted(event for event in events if isinstance(event, str)))


def _relaxed_events(case: str, output: str) -> tuple[str, tuple[Event, ...], tuple[str, ...]]:
    if case in _FACTORY_FINALIZERS:
        ordinary, finalizers = _factory_events(output, _FACTORY_FINALIZERS[case])
        return "", ordinary, finalizers
    labels = (
        _RESOURCE_FINALIZERS
        if case in {"r1792_external_exception_partial", "r1792_value_return_owner"}
        else _PROTOCOL_FINALIZERS
    )
    return _events(output, labels)


@pytest.mark.parametrize(
    "case", [path.stem for path in fixture_sources(CASES) if path.stem not in REFLECTION_CASES]
)
def test_relaxed_refactoring_preserves_program_output(case: str) -> None:
    """The same battery in relaxed mode: identical output, but for the listed finalizer watchers."""
    fixture = fixture_named(CASES, case)
    parsed_or_skipped(fixture)
    with tempfile.TemporaryDirectory(prefix="towel-hostile-relaxed-") as directory:
        root = Path(directory)
        before = root / "before" / "m.py"
        after = root / "after" / "m.py"
        before.parent.mkdir()
        after.parent.mkdir()
        shutil.copy(fixture, before)
        shutil.copy(fixture, after)
        scopes = ScopeWatch()
        applied = refactor_script(
            after,
            file_finisher=scopes,
            skip_trivial_helpers=case == "r86_annotated_assignment_live",
            strict=False,
        )
        transformed = before.read_bytes() != after.read_bytes()
        assert transformed == (applied > 0)
        assert scopes.found == []
        # Relaxed mode adds no ownership bookkeeping of its own.
        assert not OWNERSHIP_BOOKKEEPING.search(after.read_bytes()) or (
            OWNERSHIP_BOOKKEEPING.search(before.read_bytes())
        )
        if case in RELAXED_OBSERVES_FINALIZATION:
            status, output, error = _run(after)
            expected_status, expected_output, expected_error = _run(before)
            assert (status, error) == (expected_status, expected_error)
            if RELAXED_OBSERVES_FINALIZATION[case] == _ORDER and not _supported_runtime():
                # CPython 3.14 and later release a frame's variables in the
                # reverse order of 3.11 to 3.13 (the Python ownership ruling),
                # which here happens to restore the original order of release.
                assert output == expected_output, "now differs on this interpreter"
            else:
                assert output != expected_output, "no longer differs: remove it from the list"
            # The same events in the end; only where the finalizers fall differs.
            assert _relaxed_events(case, output) == _relaxed_events(case, expected_output)
        else:
            assert _run(after) == _run(before)
        if transformed:
            assert module_faces(after.parent, ["m"]) == module_faces(before.parent, ["m"])


@pytest.mark.parametrize("case", sorted(REFLECTION_CASES))
def test_reflection_does_not_veto_extraction(tmp_path: Path, case: str) -> None:
    fixture = fixture_named(CASES, case)
    parsed_or_skipped(fixture)
    script = tmp_path / "m.py"
    shutil.copy(fixture, script)
    scopes = ScopeWatch()
    applied = refactor_script(script, file_finisher=scopes)
    if lifetime_refusal_profile() and case in UNCERTIFIED_REFLECTION_REFUSALS:
        _assert_lifetime_order_refusal(script)
    if case in REFLECTION_OWNERSHIP_REFUSALS:
        # Reflection has no veto: the same ordinary ownership guard applies.
        assert applied == 0, REFLECTION_OWNERSHIP_REFUSALS[case]
    else:
        assert applied > 0, REFLECTION_CASES[case]
    compile(script.read_text(), str(script), "exec")
    assert scopes.found == []
