"""Repeated extraction never stacks helpers into a chain of forwarders.

Three mechanisms keep a run flat. A helper that returns live variables now
admits every further same-file occurrence whose call assigns them, so twelve
identical functions become one helper with twelve calls on the first pass
rather than a pair per pass. A function whose body is a block followed by a
plain ``return`` of the block's live names is reused by any site that assigns
those names, so a later pass calls it instead of restating it. And when a
site is the whole body of a helper an earlier pass inserted and neither
mechanism applies (the parameters differ), the proposal is declined: the
helper would keep only the new call, one more layer with no logic of its own.

The fixed point terminates for a reason that does not depend on any guard
declining a block for its own purposes. A helper that only calls a helper
this tool generated, unpacking and re-packing what it returns, shares no
code the user wrote, and is never extracted, whatever the settings; with
that excluded every extraction moves some of the user's code into a helper,
so extraction runs out. sqlglot once chained 597 such helpers, each
returning the previous one's tuple permuted, which the forwarding filter
did not recognize, until ordinary SQL raised RecursionError.
"""

from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path
import textwrap
from types import SimpleNamespace
from typing import Dict, List, Tuple

import pytest

from tests.test_helpers import (
    function_def,
    module_functions,
    parse_block,
    refactor_to_fixed_point_silently,
    unparsed_body,
    write_module,
)
from towel.formatting import BlackSettings, black_formatter
from towel.unification import clustering, pair_evaluation
from towel.unification.block_analysis import BlockAnalysis
from towel.unification.models import is_generated_helper_name
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.unifier import Unifier


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


def test_a_function_that_is_the_block_plus_its_return_is_reused(tmp_path: Path) -> None:
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
    assert set(functions) == {"compute", "show", "label"}, "no helper is emitted"
    assert (
        unparsed_body(functions["compute"])
        == "tmp = v + 1\nbase = tmp - 3\ntotal = base * 2\nreturn total"
    )
    assert unparsed_body(functions["show"]) == "total = compute(v)\nreturn f'show {total}'"
    assert unparsed_body(functions["label"]) == "amount = compute(w)\nreturn f'label {amount}'"


def test_a_function_returning_the_names_in_another_order_is_reused_in_its_order(
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
    assert set(functions) == {"compute", "show", "label"}, "no helper is emitted"
    assert (
        unparsed_body(functions["compute"])
        == "lo = v - 1\nhi = v + 1\nmid = (lo + hi) / 2\nreturn (lo, hi)"
    )
    assert unparsed_body(functions["show"]) == "lo, hi = compute(v)\nreturn f'show {lo} {hi}'"
    assert unparsed_body(functions["label"]) == "lo, hi = compute(v)\nreturn f'label {lo} {hi}'"


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


# sqlglot's generator, reduced: each method reads ``expression.this`` and two
# arguments, and the keys are long enough that a call passing them wraps over
# the line minimum once formatted. Every extraction left call sites that
# unpack a helper's tuple in their own order.
SQL_GENERATOR = """
class Expr:
    def __init__(self, this, **args):
        self.this = this
        self.args = args


class Generator:
    def func(self, name, *args):
        return f"{name}({', '.join(map(str, args))})"

    def zipf_sql(self, expression):
        s = expression.this
        n = expression.args["elementcount_argument_key"]
        gen = expression.args["gen_argument_key"]
        return self.func("ZIPF", gen, n, s)

    def tobinary_sql(self, expression):
        value = expression.this
        format_arg = expression.args.get("format_argument_key")
        is_safe = expression.args.get("safe_argument_key")
        return self.func("TO_BINARY", is_safe, format_arg, value)

    def sortarray_sql(self, expression):
        arr = expression.this
        asc = expression.args.get("asc_argument_key")
        nulls_first = expression.args.get("nulls_first_argument_key")
        return self.func("LIST_SORT", arr, asc, nulls_first)

    def strposition_sql(self, expression):
        this = expression.this
        substr = expression.args.get("substr_argument_key")
        position = expression.args.get("position_argument_key")
        return self.func("STRPOS", substr, position, this)

    def round_sql(self, expression):
        this = expression.this
        decimals = expression.args.get("decimals_argument_key")
        truncate = expression.args.get("truncate_argument_key")
        return self.func("ROUND", decimals, this, truncate)

    def splitpart_sql(self, expression):
        string_arg = expression.this
        delimiter_arg = expression.args.get("delimiter_argument_key")
        part_index_arg = expression.args.get("part_index_argument_key")
        return self.func("SPLIT_PART", part_index_arg, delimiter_arg, string_arg)
"""

SQL_KEYS = {
    "zipf_sql": ("elementcount", "gen"),
    "tobinary_sql": ("format", "safe"),
    "sortarray_sql": ("asc", "nulls_first"),
    "strposition_sql": ("substr", "position"),
    "round_sql": ("decimals", "truncate"),
    "splitpart_sql": ("delimiter", "part_index"),
}


def _rendered_sql(source: str) -> Dict[str, str]:
    """What each generator method renders for an expression carrying its two arguments."""
    namespace: Dict[str, object] = {}
    exec(compile(source, "<generator>", "exec"), namespace)
    generator = namespace["Generator"]()  # type: ignore[operator]
    expression_type = namespace["Expr"]
    return {
        method: getattr(generator, method)(
            expression_type("x", **{f"{key}_argument_key": index for index, key in enumerate(keys)})  # type: ignore[operator]
        )
        for method, keys in SQL_KEYS.items()
    }


def _generated_helpers(source: str) -> List[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and is_generated_helper_name(node.name)
    ]


def _refactor_generator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    escape_guard: bool,
    min_lines: int = 1,
    formatted: bool = False,
    bound: int = 25,
    skip_trivial_helpers: bool = True,
) -> Tuple[str, int]:
    """Refactor ``SQL_GENERATOR`` for at most ``bound`` applications.

    With ``escape_guard`` false the guard that declines a block whose lambdas
    could be looked at is switched off. It declined the blocks that fed the
    sqlglot chain only incidentally, because they pass fresh lambdas to a
    call; termination must not rest on it.
    """
    if not escape_guard:
        monkeypatch.setattr(pair_evaluation, "created_object_escapes", lambda *_: False)
        monkeypatch.setattr(clustering, "created_object_escapes", lambda *_: False)
    engine = UnificationRefactorEngine(
        min_lines=min_lines,
        snippet_formatter=black_formatter(BlackSettings()) if formatted else None,
        skip_trivial_helpers=skip_trivial_helpers,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        final, applied, _descriptions = engine.refactor_to_fixed_point(
            write_module(tmp_path, SQL_GENERATOR, "generator.py"),
            max_iterations=bound,
            progress="none",
        )
    return final, applied


_PLUMBING = (
    ast.Assign,
    ast.Return,
    ast.Expr,
    ast.Name,
    ast.Constant,
    ast.Tuple,
    ast.List,
    ast.Starred,
    ast.Lambda,
    ast.arguments,
    ast.keyword,
    ast.expr_context,
)


def _only_forwards_to_generated_helpers(helper: ast.FunctionDef) -> bool:
    """Whether the helper computes nothing but calls of generated helpers and of its thunks."""
    generated_calls = 0
    for statement in helper.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                callee = node.func
                name = (
                    callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
                )
                if is_generated_helper_name(name):
                    generated_calls += 1
                elif not name.startswith("__param_"):
                    return False
            elif isinstance(node, ast.Attribute):
                if not is_generated_helper_name(node.attr):
                    return False
            elif not isinstance(node, _PLUMBING):
                return False
    return generated_calls > 0


@pytest.mark.parametrize("skip_trivial_helpers", [True, False])
def test_the_fixed_point_ends_without_the_escape_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skip_trivial_helpers: bool
) -> None:
    # On the unfixed engine this shape extracts a helper per pass until the
    # bound: each forwards to the previous one and returns its tuple permuted.
    final, applied = _refactor_generator(
        tmp_path, monkeypatch, escape_guard=False, skip_trivial_helpers=skip_trivial_helpers
    )
    assert applied < 25, "the run reached a fixed point"
    forwarders = [
        helper.name
        for helper in _generated_helpers(final)
        if _only_forwards_to_generated_helpers(helper)
    ]
    assert forwarders == []
    assert _rendered_sql(final) == _rendered_sql(SQL_GENERATOR)


def test_a_forwarded_thunk_is_passed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``zipf_sql`` subscripts where the others call ``get``; the helper that
    # takes the whole difference as thunks passes them on as it got them
    # instead of wrapping each as ``lambda: __param_0()``.
    final, _applied = _refactor_generator(tmp_path, monkeypatch, escape_guard=False)
    rewrapped = [
        ast.unparse(node)
        for helper in _generated_helpers(final)
        for node in ast.walk(helper)
        if isinstance(node, ast.Lambda)
        and not node.args.args
        and isinstance(node.body, ast.Call)
        and isinstance(node.body.func, ast.Name)
        and node.body.func.id.startswith("__param_")
        and not node.body.args
    ]
    assert rewrapped == []
    assert _rendered_sql(final) == _rendered_sql(SQL_GENERATOR)


@pytest.mark.parametrize("formatted", [True, False])
def test_sqlglots_generator_ends_after_one_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, formatted: bool
) -> None:
    # The r5_chain reproducer as ``towel dry`` runs it: formatted, three lines.
    final, applied = _refactor_generator(
        tmp_path, monkeypatch, escape_guard=True, min_lines=3, formatted=formatted
    )
    assert applied == 1
    assert len(_generated_helpers(final)) == 1
    assert _rendered_sql(final) == _rendered_sql(SQL_GENERATOR)


@pytest.mark.parametrize(
    "body",
    [
        "a, b, c = self._extracted_func_3(p, q, e)\nreturn (c, a, b)",
        "a, b, c = forward(p, q, e)\nreturn (b, a)",
        "a, b = forward(p)\nreturn [b, a]",
        "(a, b), c = forward(p)\nreturn (c, b, a)",
    ],
)
def test_a_call_whose_results_are_returned_rearranged_only_forwards(body: str) -> None:
    helper = function_def("def helper(self, p, q, e):\n" + textwrap.indent(body, "    "))
    assert BlockAnalysis._helper_is_trivial_forwarding(helper)


@pytest.mark.parametrize(
    "body",
    [
        "a, b = forward(p)\nreturn (b, a + 1)",
        "a, *rest = forward(p)\nreturn (a, rest)",
        "a, b = forward(p)\nreturn (b, other)",
    ],
)
def test_a_call_whose_results_are_computed_on_is_not_forwarding(body: str) -> None:
    helper = function_def("def helper(p, other):\n" + textwrap.indent(body, "    "))
    assert not BlockAnalysis._helper_is_trivial_forwarding(helper)


_GENERATED_CALLER = "def helper(self, __param_0, __param_1, e, rest, callback):\n"


@pytest.mark.parametrize(
    "body",
    [
        "a, b, c = self._extracted_func_3(__param_0, __param_1, e)\nreturn (c, a, b)",
        "return __extracted_func_0(lambda: __param_0(), __param_1, 'key', *rest)",
        "x = __extracted_func_0(__param_0)\ny = __extracted_func_1(x, key=__param_1)\nreturn (y, x)",
        "__extracted_func_2(__extracted_func_1(__param_0), lambda: __param_1)",
        # sqlglot with the escape guard off: a thunk the site passes is
        # evaluated beside the call, which is still the site's code.
        "a, b = self._extracted_func_0(__param_0, e)\nc = __param_1()\nreturn (a, b, c)",
    ],
)
def test_a_helper_that_only_calls_generated_helpers_shares_nothing(body: str) -> None:
    helper = function_def(_GENERATED_CALLER + textwrap.indent(body, "    "))
    assert BlockAnalysis._helper_only_calls_generated_helpers(helper)


@pytest.mark.parametrize(
    "body",
    [
        # The user's own code: an attribute read, an operator, another call.
        "a, b = self._extracted_func_3(__param_0.key, __param_1)\nreturn (b, a)",
        "return __extracted_func_0(__param_0) + 1",
        "x = __extracted_func_0(__param_0)\nreturn self.func(x)",
        "return __extracted_func_0(lambda: e.args.get(__param_0))",
        # A list display makes a new list; a free name the user calls is theirs.
        "x = __extracted_func_0(__param_0)\nitems = []\nreturn (x, items)",
        "x = __extracted_func_0(__param_0)\ny = callback()\nreturn (x, y)",
        # No generated helper at all: the forwarding filter's concern.
        "return forward(__param_0, __param_1)",
    ],
)
def test_a_helper_with_code_of_its_own_is_not_plumbing(body: str) -> None:
    helper = function_def(_GENERATED_CALLER + textwrap.indent(body, "    "))
    assert not BlockAnalysis._helper_only_calls_generated_helpers(helper)


def _lambda_parameters(first: str, second: str) -> List[str]:
    """What each parameter stands for in the first block, when the blocks unify."""
    substitution = Unifier().unify_blocks([parse_block(first), parse_block(second)], [{}, {}])
    assert substitution is not None
    return sorted(
        ast.unparse(expression)
        for expressions in substitution.param_expressions.values()
        for index, expression in expressions
        if index == 0
    )


def test_a_lambda_handed_to_a_call_is_the_parameter_itself() -> None:
    assert _lambda_parameters(
        "r = run(lambda: e.args['a'], e)", "r = run(lambda: e.args.get('b'), e)"
    ) == ["lambda: e.args['a']"]
    assert _lambda_parameters(
        "r = run(key=lambda: e.args['a'])", "r = run(key=lambda: e.args.get('b'))"
    ) == ["lambda: e.args['a']"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        # Made on every iteration: one lambda passed in would stand for many.
        (
            "for i in r:\n    run(lambda: e.args['a'])",
            "for i in r:\n    run(lambda: e.args.get('b'))",
        ),
        # Two lambdas of one text would become one object.
        (
            "run(lambda: e.args['a'], lambda: e.args['a'])",
            "run(lambda: e.args.get('b'), lambda: e.args.get('b'))",
        ),
        # Called where it stands, not handed on.
        ("r = (lambda: e.args['a'])()", "r = (lambda: e.args.get('b'))()"),
        # Only part of the body differs.
        ("r = run(lambda: e.args['a'])", "r = run(lambda: e.args['b'])"),
        # Only a free name differs: the names correspond, and the helper
        # would no longer read the one the call sites pass.
        ("r = run(lambda: first, e)", "r = run(lambda: second, e)"),
    ],
)
def test_a_lambda_is_otherwise_kept_and_its_body_parameterized(first: str, second: str) -> None:
    assert not any(
        parameter.startswith("lambda") for parameter in _lambda_parameters(first, second)
    )
