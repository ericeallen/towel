"""The pure parts of the annotation ladder: what each rung writes, and what ends the ladder.

The end-to-end tests run the real checker over small projects; these pin each
rule against a helper and a refusal written out by hand, so a rule's edge is
tested where it is decided.
"""

from __future__ import annotations

import ast
import textwrap

from towel.unification.annotation_ladder import (
    narrowing_needed_in_thunk,
    partial_type_passed,
)
from towel.unification.exceptions import Untypeable
from towel.unification.placement import method_helper_position


def _function(source: str, index: int = 0) -> ast.FunctionDef:
    """The ``index``-th statement of ``source``, which must be a function definition."""
    node = ast.parse(textwrap.dedent(source)).body[index]
    assert isinstance(node, ast.FunctionDef)
    return node


# -- The judgments made from the proposal alone ------------------------------------


_HELPER = _function("""
        def helper(__param_0, __param_1, __param_2):
            value = __param_1() if __param_0 else __param_2()
            return value
        """)


def _call(source: str) -> ast.Call:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.Call)
    return node


def test_a_test_argument_whose_lambda_reads_what_it_narrows_is_named() -> None:
    call = _call("helper(task.total is not None, lambda: int(task.total), lambda: done)")
    verdict = narrowing_needed_in_thunk(_HELPER, [call])
    assert verdict is not None and verdict.reason is Untypeable.NARROWING_READ_IN_THUNK


def test_a_flag_or_a_lambda_reading_something_else_is_left_to_the_checker() -> None:
    flag = _call("helper(finished, lambda: int(task.total), lambda: done)")
    other = _call("helper(task.total is not None, lambda: int(task.done), lambda: done)")
    assert narrowing_needed_in_thunk(_HELPER, [flag, other]) is None


def _partial(source: str, *, checked_untyped: bool = False) -> object:
    text = textwrap.dedent(source).lstrip()
    module = ast.parse(text)
    call = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "helper"
    )
    return partial_type_passed([("m.py", text, call.lineno, call)], lambda _: checked_untyped)


def test_an_empty_collection_passed_before_anything_fills_it_is_named() -> None:
    verdict = _partial("""
        def f(options: dict[str, int]) -> None:
            attrs = {}
            helper(attrs, options)
        """)
    assert getattr(verdict, "reason", None) is Untypeable.PARTIAL_TYPE


def test_a_collection_filled_or_declared_before_the_call_is_not_partial() -> None:
    filled = """
        def f(options: dict[str, int]) -> None:
            attrs = {}
            attrs["a"] = 1
            helper(attrs, options)
        """
    declared = """
        def f(options: dict[str, int]) -> None:
            attrs: dict[str, int] = {}
            helper(attrs, options)
        """
    parameter = """
        def f(attrs: dict[str, int]) -> None:
            helper(attrs, attrs)
        """
    for source in (filled, declared, parameter):
        assert _partial(source) is None, source


def test_a_body_mypy_does_not_check_is_left_alone() -> None:
    source = """
        def f(options):
            attrs = {}
            helper(attrs, options)
        """
    assert _partial(source) is None
    assert (
        getattr(_partial(source, checked_untyped=True), "reason", None) is Untypeable.PARTIAL_TYPE
    )


# -- Where the helper goes ---------------------------------------------------------

_METHOD_HELPER = _function("""
    def __extracted_func_0(self, value: int) -> None:
        self.value = value
        self.twice = value * 2
    """)


def _position(body: str) -> object:
    source = "class Box:\n" + textwrap.indent(textwrap.dedent(body).lstrip(), "    ")
    return method_helper_position(source, "Box", _METHOD_HELPER, "self")


def test_no_later_assignment_leaves_the_helper_at_the_end_of_the_class() -> None:
    assert _position("""
            def __init__(self, value: int) -> None:
                self.__extracted_func_0(value)

            def reset(self) -> None:
                self.__extracted_func_0(0)
            """) is None


def test_an_assignment_before_the_first_call_keeps_the_declaration_where_it_was() -> None:
    assert _position("""
            def __init__(self) -> None:
                self.value = 0
                self.twice = 0

            def set(self, value: int) -> None:
                self.__extracted_func_0(value)

            def clear(self) -> None:
                self.value = -1
            """) is None


def test_a_later_assignment_puts_the_helper_right_after_the_first_caller() -> None:
    # __init__ ends on line 3 of the module ("class Box:" is line 1).
    assert _position("""
            def __init__(self, value: int) -> None:
                self.__extracted_func_0(value)

            def restore(self, state: object) -> None:
                self.value, self.twice = state  # type: ignore[misc]
            """) == 3


def test_the_first_callers_own_later_assignment_puts_the_helper_before_it() -> None:
    assert _position("""
            def __init__(self, value: int) -> None:
                self.__extracted_func_0(value)
                self.twice = value
            """) == 1


def test_attributes_pulling_both_ways_leave_the_helper_where_it_was() -> None:
    assert _position("""
            def __init__(self, value: int) -> None:
                self.value = value
                self.__extracted_func_0(value)
                self.twice = value
            """) is None
