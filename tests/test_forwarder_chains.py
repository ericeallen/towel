"""Repeated extraction never stacks helpers into a chain of forwarders.

Two mechanisms keep a run flat. A helper that returns live variables now
admits every further same-file occurrence whose call assigns them, so twelve
identical functions become one helper with twelve calls on the first pass
rather than a pair per pass; a function whose body is such a block followed
by a plain ``return`` of the block's live names takes the same helper, and
is never made to call another existing function instead. And when a site is
the whole body of a helper an earlier pass inserted while another site is
not a whole body, the proposal is declined: the helper would keep only the
new call, one more layer with no logic of its own.
"""

from __future__ import annotations

from pathlib import Path
import textwrap
from types import SimpleNamespace

from tests.test_helpers import (
    module_functions,
    refactor_to_fixed_point_silently,
    unparsed_body,
    write_module,
)


def _summaries(source: str, names: list[str]) -> list[object]:
    namespace: dict[str, object] = {}
    exec(compile(source, "<chain>", "exec"), namespace)
    order = SimpleNamespace(
        id=7,
        items=[
            SimpleNamespace(price=10.0, in_stock=True),
            SimpleNamespace(price=5.0, in_stock=False),
        ],
    )
    return [namespace[name](order) for name in names]  # type: ignore[operator]


def _chain(count: int) -> str:
    return "\n".join(f"""
        def f{index}(order):
            items = [i for i in order.items if i.in_stock]
            subtotal = sum(i.price for i in items)
            total = round(subtotal * 1.08, 2)
            return f"F{index} {{order.id}}: ${{total}}"
        """ for index in range(count))


def test_identical_blocks_with_a_live_variable_share_one_helper(tmp_path: Path) -> None:
    source = textwrap.dedent(_chain(12))
    final, applied = refactor_to_fixed_point_silently(write_module(tmp_path, source))
    functions = module_functions(final)
    helpers = [name for name in functions if name.startswith("__extracted_func")]
    assert helpers == ["__extracted_func_0"], "one helper serves every site"
    assert applied == 1
    for index in range(12):
        assert unparsed_body(functions[f"f{index}"]).startswith("total = __extracted_func_0(order)")
    names = [f"f{index}" for index in range(12)]
    assert _summaries(final, names) == _summaries(source, names)


def test_clustered_sites_keep_their_own_spelling_of_the_returned_name(tmp_path: Path) -> None:
    final, _applied = refactor_to_fixed_point_silently(
        write_module(
            tmp_path,
            """
            def a(v):
                tmp = v + 1
                base = tmp - 3
                total = base * 2
                return f"a {total}"

            def b(v):
                tmp = v + 1
                base = tmp - 3
                total = base * 2
                return f"b {total}"

            def c(v):
                step = v + 1
                offset = step - 3
                amount = offset * 2
                return f"c {amount}"
            """,
        ),
        3,
    )
    functions = module_functions(final)
    assert [name for name in functions if name.startswith("__extracted_func")] == [
        "__extracted_func_0"
    ]
    assert unparsed_body(functions["c"]) == "amount = __extracted_func_0(v)\nreturn f'c {amount}'"


def test_the_helper_returns_what_every_clustered_site_reads(tmp_path: Path) -> None:
    # The proposal built from the pair whose union of live names covers the
    # third site wins; a candidate reading a name its helper does not return
    # is declined for that helper (see ``align_return_variables``).
    final, _applied = refactor_to_fixed_point_silently(
        write_module(
            tmp_path,
            """
            def a(v):
                tmp = v + 1
                base = tmp - 3
                total = base * 2
                return f"a {total}"

            def b(v):
                tmp = v + 1
                base = tmp - 3
                total = base * 2
                return f"b {total}"

            def c(v):
                tmp = v + 1
                base = tmp - 3
                total = base * 2
                return f"c {total} {tmp}"
            """,
        ),
        3,
    )
    functions = module_functions(final)
    assert [name for name in functions if name.startswith("__extracted_func")] == [
        "__extracted_func_0"
    ]
    for name in ("a", "b", "c"):
        assert unparsed_body(functions[name]).startswith("tmp, total = __extracted_func_0(v)")


def test_a_function_that_is_the_block_plus_its_return_shares_the_helper(tmp_path: Path) -> None:
    final, _applied = refactor_to_fixed_point_silently(
        write_module(
            tmp_path,
            """
            def compute(v):
                tmp = v + 1
                base = tmp - 3
                total = base * 2
                return total

            def show(v):
                tmp = v + 1
                base = tmp - 3
                total = base * 2
                return f"show {total}"

            def label(w):
                step = w + 1
                offset = step - 3
                amount = offset * 2
                return f"label {amount}"
            """,
        ),
        3,
    )
    functions = module_functions(final)
    assert set(functions) == {"compute", "show", "label", "__extracted_func_0"}
    assert unparsed_body(functions["compute"]) == "total = __extracted_func_0(v)\nreturn total"
    assert (
        unparsed_body(functions["show"]) == "total = __extracted_func_0(v)\nreturn f'show {total}'"
    )
    # ``label`` spells its parameter ``w``, so it does not join the helper on
    # the pass that inserts it; a later pass would have to reduce that helper
    # to a forwarder or redirect ``label`` to it, and does neither.
    assert (
        unparsed_body(functions["label"])
        == "step = w + 1\noffset = step - 3\namount = offset * 2\nreturn f'label {amount}'"
    )


def test_a_function_returning_the_names_in_another_order_keeps_its_order(
    tmp_path: Path,
) -> None:
    final, _applied = refactor_to_fixed_point_silently(
        write_module(
            tmp_path,
            """
            def compute(v):
                lo = v - 1
                hi = v + 1
                mid = (lo + hi) / 2
                return lo, hi

            def show(v):
                lo = v - 1
                hi = v + 1
                mid = (lo + hi) / 2
                return f"show {lo} {hi}"

            def label(v):
                lo = v - 1
                hi = v + 1
                mid = (lo + hi) / 2
                return f"label {lo} {hi}"
            """,
        ),
        3,
    )
    functions = module_functions(final)
    assert set(functions) == {"compute", "show", "label", "__extracted_func_0"}
    assert unparsed_body(functions["compute"]) == "hi, lo = __extracted_func_0(v)\nreturn (lo, hi)"
    assert (
        unparsed_body(functions["show"])
        == "hi, lo = __extracted_func_0(v)\nreturn f'show {lo} {hi}'"
    )
    assert (
        unparsed_body(functions["label"])
        == "hi, lo = __extracted_func_0(v)\nreturn f'label {lo} {hi}'"
    )


GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT = """
def __extracted_func_0(result, x):
    y = x + 10
    z = y ** 2
    result.append(z)

def collect(items, offset):
    results = []
    for item in items:
        step = item + offset
        final = step ** 2
        results.append(final)
    return results
"""


def test_a_generated_helper_is_not_reduced_to_a_forwarder(tmp_path: Path) -> None:
    final, applied = refactor_to_fixed_point_silently(
        write_module(tmp_path, GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT), min_lines=3
    )
    assert applied == 0
    assert (
        unparsed_body(module_functions(final)["__extracted_func_0"])
        == "y = x + 10\nz = y ** 2\nresult.append(z)"
    )


def test_the_forwarder_check_is_part_of_skipping_trivial_helpers(tmp_path: Path) -> None:
    final, applied = refactor_to_fixed_point_silently(
        write_module(tmp_path, GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT),
        min_lines=3,
        skip_trivial_helpers=False,
    )
    assert applied == 1
    assert (
        unparsed_body(module_functions(final)["__extracted_func_0"])
        == "__extracted_func_1(x, 10, result)"
    )


def test_a_user_named_function_may_specialize_the_new_helper(tmp_path: Path) -> None:
    # Only helpers this tool inserted are protected; a named function that
    # becomes a one-line specialization of the helper is a legitimate result.
    final, applied = refactor_to_fixed_point_silently(
        write_module(
            tmp_path,
            GENERATED_HELPER_WITH_A_DIFFERENT_CONSTANT.replace(
                "__extracted_func_0", "square_shifted"
            ),
        ),
        3,
    )
    assert applied == 1
    assert (
        unparsed_body(module_functions(final)["square_shifted"])
        == "__extracted_func_0(x, 10, result)"
    )
