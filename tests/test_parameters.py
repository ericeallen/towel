"""The shared parameter enumerators must cover every argument category.

refactor_engine._collect_parameter_names, binding_detector._bind_function_parameters,
scope_analyzer, unifier, definite_assignment, and assignment_analyzer all rely on
these, so a gap here would silently drop bindings across the analysis.
"""

import ast

from towel.unification.parameters import parameter_names, parameter_nodes


def _args(src: str) -> ast.arguments:
    return ast.parse(src).body[0].args  # type: ignore[attr-defined]


def test_parameter_names_covers_every_category():
    args = _args("def f(a, /, b, c=1, *args, d, e=2, **kw): pass")
    assert set(parameter_names(args)) == {"a", "b", "c", "args", "d", "e", "kw"}


def test_parameter_nodes_yields_in_declaration_group_order():
    args = _args("def f(a, /, b, *args, c, **kw): pass")
    # positional-only, positional, keyword-only, *args, **kwargs
    assert [node.arg for node in parameter_nodes(args)] == ["a", "b", "c", "args", "kw"]


def test_parameter_names_empty_for_no_arguments():
    assert set(parameter_names(_args("def f(): pass"))) == set()
