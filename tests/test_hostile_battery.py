"""Execute hostile fixtures before and after fixed-point refactoring.

Each fixture in ``tests/hostile_cases`` is a script whose ``__main__`` block
prints every observation that an extraction could disturb: evaluation order,
evaluation count, conditional evaluation, closure cells, deletion, and
pattern bindings. The battery asserts that the program's output is identical
after refactoring, and records per fixture whether the current engine
transforms it or rejects it, so a change in either direction is visible.
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
    "r85_conditionally_bound_parameter",
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
}
# Every other fixture must come back byte-identical. One of them once changed:
# r86_annotated_assignment_live left the set when the trivial-helper filter
# began declining its shared block, which binds only a literal and a parameter.


def _run(script: Path) -> tuple[int, str, list[str]]:
    return observe(script.name, script.parent)


@pytest.mark.parametrize("case", sorted(path.stem for path in CASES.glob("*.py")))
def test_refactoring_preserves_program_output(case: str) -> None:
    with tempfile.TemporaryDirectory(prefix="towel-hostile-") as directory:
        root = Path(directory)
        before = root / "before" / "m.py"
        after = root / "after" / "m.py"
        before.parent.mkdir()
        after.parent.mkdir()
        shutil.copy(CASES / f"{case}.py", before)
        shutil.copy(CASES / f"{case}.py", after)
        engine = UnificationRefactorEngine(min_lines=3)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            _, applied, _ = engine.refactor_to_fixed_point(str(after))
        transformed = before.read_bytes() != after.read_bytes()
        assert transformed == (applied > 0)
        assert _run(after) == _run(before)
        assert transformed == (case in TRANSFORMED), (
            "rejected" if not transformed else "transformed"
        )
