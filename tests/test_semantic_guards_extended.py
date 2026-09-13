"""Unit tests for deletion, closure-boundary, and eager-parameter guards."""

from __future__ import annotations

import ast
from typing import cast

import pytest

from towel.unification.semantic_safety import (
    defer_impure_parameters,
    has_impure_eager_parameters,
    is_eagerly_evaluable,
    moves_scope_declaration,
    nested_scopes_cross_block_boundary,
    unbinds_external_name,
)
from towel.unification.unifier import Substitution


def _function(source: str) -> ast.FunctionDef:
    return cast(ast.FunctionDef, ast.parse(source).body[0])


def _slice(function: ast.FunctionDef, start: int, stop: int) -> list[ast.AST]:
    return list(function.body[start:stop])


class TestUnbindsExternalName:
    def test_explicit_del_of_prebound_name(self) -> None:
        function = _function("def f(v):\n    x = v\n    print(x)\n    del x\n    return 1\n")
        assert unbinds_external_name(function, _slice(function, 1, 3), {"x", "v"})
        assert not unbinds_external_name(function, _slice(function, 1, 3), {"v"})

    def test_except_clause_unbinds_prebound_name(self) -> None:
        function = _function(
            "def f(v):\n    e = 'orig'\n    try:\n        r = 1 / v\n"
            "    except ZeroDivisionError as e:\n        r = None\n    return e\n"
        )
        assert unbinds_external_name(function, _slice(function, 1, 2), {"e", "v"})
        assert not unbinds_external_name(function, _slice(function, 1, 2), {"v"})

    def test_global_name_deleted_in_block(self) -> None:
        function = _function("def f():\n    global state\n    state = 1\n    del state\n")
        assert unbinds_external_name(function, _slice(function, 1, 3), set())


class TestNestedScopesCrossBlockBoundary:
    def test_block_rebinds_cell_read_by_earlier_closure(self) -> None:
        function = _function(
            "def f(items):\n    def show():\n        return factor\n"
            "    out = [show for _ in items]\n    factor = 10\n    return out\n"
        )
        assert nested_scopes_cross_block_boundary(function, _slice(function, 1, 3))

    def test_closure_inside_block_reads_name_rebound_after(self) -> None:
        function = _function(
            "def f(items):\n    factor = 2\n    def scale(v):\n        return v * factor\n"
            "    factor = 3\n    return [scale(i) for i in items]\n"
        )
        assert nested_scopes_cross_block_boundary(function, _slice(function, 1, 2))

    def test_closure_inside_block_reading_earlier_binding_is_allowed(self) -> None:
        function = _function(
            "def f(items):\n    factor = 2\n    def scale(v):\n        return v * factor\n"
            "    return [scale(i) for i in items]\n"
        )
        assert not nested_scopes_cross_block_boundary(function, _slice(function, 1, 3))

    def test_block_and_closure_with_disjoint_names_are_allowed(self) -> None:
        function = _function(
            "def f(items):\n    def show():\n        return other\n"
            "    total = sum(items)\n    return show, total\n"
        )
        assert not nested_scopes_cross_block_boundary(function, _slice(function, 1, 2))

    def test_nested_block_closure_with_any_outside_binding_is_rejected(self) -> None:
        function = _function(
            "def f(items):\n    limit = 0\n    for i in items:\n"
            "        check = lambda v: v < limit\n        use(check)\n        limit = i\n"
        )
        loop = cast(ast.For, function.body[1])
        assert nested_scopes_cross_block_boundary(function, list(loop.body[:2]))


@pytest.mark.parametrize(
    "source, expected",
    [
        ("x", True),
        ("1", True),
        ("-1", True),
        ("not True", True),
        ("(a, 1)", True),
        ("[a, b]", True),
        ("a.b", False),
        ("a[0]", False),
        ("a + 1", False),
        ("f()", False),
        ("[x for x in xs]", False),
        ("lambda: 1", False),
        ("(a, b())", False),
        ("-x", False),
    ],
)
def test_is_eagerly_evaluable(source: str, expected: bool) -> None:
    assert is_eagerly_evaluable(ast.parse(source, mode="eval").body) is expected


def _substitution(*expressions: str, callee: bool = False) -> tuple[Substitution, list[ast.AST]]:
    substitution = Substitution()
    for index, source in enumerate(expressions):
        substitution.add_mapping(index, ast.parse(source, mode="eval").body, "__param_0")
    template_source = f"value = {expressions[0]}(1)\n" if callee else f"value = {expressions[0]}\n"
    return substitution, list(ast.parse(template_source).body)


def test_impure_arguments_become_thunks() -> None:
    substitution, template = _substitution("self.email", "self.phone")
    defer_impure_parameters(substitution, template)
    assert substitution.function_params == {"__param_0": []}
    assert not has_impure_eager_parameters(substitution)


def test_pure_arguments_stay_eager() -> None:
    substitution, template = _substitution("k", "k")
    defer_impure_parameters(substitution, template)
    assert substitution.function_params == {}
    assert not has_impure_eager_parameters(substitution)


def test_callee_parameters_are_forwarded_not_thunked() -> None:
    substitution, template = _substitution("obj.strip", "obj.upper", callee=True)
    defer_impure_parameters(substitution, template)
    assert substitution.function_params == {}
    substitution.params_used_as_callee.add("__param_0")
    assert not has_impure_eager_parameters(substitution)


def test_undeferred_impure_argument_is_detected() -> None:
    substitution, _ = _substitution("a.b", "c.d")
    assert has_impure_eager_parameters(substitution)


class TestMovesScopeDeclaration:
    def test_declaration_used_after_block_is_rejected(self) -> None:
        function = _function(
            "def f(items):\n    global counter\n    for i in items:\n        counter += i\n"
            "    counter += 1\n    return counter\n"
        )
        assert moves_scope_declaration(function, _slice(function, 0, 2))

    def test_declaration_fully_inside_block_is_allowed(self) -> None:
        function = _function(
            "def f(items):\n    global counter\n    for i in items:\n        counter += i\n"
            "    return len(items)\n"
        )
        assert not moves_scope_declaration(function, _slice(function, 0, 2))

    def test_nested_function_declaration_moves_with_it(self) -> None:
        function = _function(
            "def f(items):\n    count = 0\n    def inc():\n        nonlocal count\n"
            "        count += 1\n    for i in items:\n        inc()\n    return count\n"
        )
        assert not moves_scope_declaration(function, _slice(function, 1, 3))


class TestAlignReturnVariables:
    def test_union_is_ordered_by_template_names_and_mapped(self) -> None:
        from towel.unification.refactor_engine import _align_return_variables

        renames = [{"zeta": "__temp_0", "alpha": "__temp_1"}, {"lo": "__temp_0", "hi": "__temp_1"}]
        aligned = _align_return_variables(
            {"zeta", "total"},
            {"hi", "total"},
            {"zeta", "alpha", "total"},
            {"lo", "hi", "total"},
            renames,
        )
        assert aligned == (["alpha", "total", "zeta"], ["hi", "total", "lo"])

    def test_variable_unbound_in_other_block_is_rejected(self) -> None:
        from towel.unification.refactor_engine import _align_return_variables

        assert _align_return_variables({"x"}, set(), {"x"}, {"y"}, [{}, {}]) is None
