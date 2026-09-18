"""Unit tests for the top-level AST visitors in ``towel.unification.visitors``.

Each visitor is driven through its public surface: construct it, ``visit`` a
parsed tree, and read the result it publishes. ``ClassCollector`` is not
covered here because it is being removed.
"""

from __future__ import annotations

import ast
from typing import List, Optional, Tuple, Union

import pytest

from towel.unification.visitors import (
    AssignTargetVisitor,
    AugAssignFinder,
    ClassLocator,
    FuncLocator,
    FunctionCollector,
    LoopReturnFinder,
    MethodCallRewriter,
    MethodKind,
    NameCollector,
)

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
Record = Tuple[str, Optional[str], Optional[str], List[str]]


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


# ---------------------------------------------------------------------------
# FunctionCollector


def _collect_functions(source: str) -> List[Record]:
    records: List[Record] = []

    def sink(
        node: FunctionNode, class_name: Optional[str], enclosing: Optional[str], ancestry: List[str]
    ) -> None:
        records.append((node.name, class_name, enclosing, list(ancestry)))

    FunctionCollector(sink).visit(_parse(source))
    return records


def test_function_collector_reports_class_enclosing_function_and_ancestry() -> None:
    records = _collect_functions(
        "def top():\n"
        "    def inner():\n"
        "        def deeper():\n"
        "            pass\n"
        "\n"
        "class C:\n"
        "    def method(self):\n"
        "        def local():\n"
        "            pass\n"
        "    async def amethod(self):\n"
        "        pass\n"
        "\n"
        "def after():\n"
        "    pass\n"
    )
    assert records == [
        ("top", None, None, []),
        ("inner", None, "top", ["top"]),
        ("deeper", None, "inner", ["top", "inner"]),
        ("method", "C", None, []),
        ("local", "C", "method", ["method"]),
        ("amethod", "C", None, []),
        ("after", None, None, []),
    ]


def test_function_collector_restores_context_after_a_class_inside_a_function() -> None:
    records = _collect_functions(
        "def outer():\n"
        "    class Local:\n"
        "        def method(self):\n"
        "            pass\n"
        "    def sibling():\n"
        "        pass\n"
    )
    assert records == [
        ("outer", None, None, []),
        ("method", "Local", "outer", ["outer"]),
        ("sibling", None, "outer", ["outer"]),
    ]


def test_function_collector_ancestry_excludes_the_function_itself() -> None:
    records = _collect_functions("def only():\n    pass\n")
    assert records == [("only", None, None, [])]


# ---------------------------------------------------------------------------
# MethodCallRewriter


def _drop_positional(args: List[ast.expr], implicit_name: str) -> List[ast.expr]:
    return [a for a in args if not (isinstance(a, ast.Name) and a.id == implicit_name)]


def _drop_keyword(keywords: List[ast.keyword], implicit_name: str) -> List[ast.keyword]:
    return [k for k in keywords if k.arg != implicit_name]


def _rewrite(
    source: str,
    *,
    method_kind: Optional[MethodKind] = "instance",
    implicit_name: Optional[str] = None,
    class_name: Optional[str] = None,
) -> str:
    rewriter = MethodCallRewriter(
        _drop_positional,
        _drop_keyword,
        original_name="helper",
        new_name="_helper",
        method_kind=method_kind,
        implicit_name=implicit_name,
        class_name=class_name,
    )
    return ast.unparse(rewriter.visit(_parse(source)))


def test_method_call_rewriter_turns_instance_helper_calls_into_self_dispatch() -> None:
    assert _rewrite("helper(self, x, y=1)") == "self._helper(x, y=1)"


def test_method_call_rewriter_drops_the_implicit_argument_passed_by_keyword() -> None:
    assert _rewrite("helper(x, self=self)") == "self._helper(x)"


def test_method_call_rewriter_uses_cls_for_classmethods() -> None:
    assert _rewrite("helper(cls, x)", method_kind="classmethod") == "cls._helper(x)"


def test_method_call_rewriter_honours_an_explicit_implicit_name() -> None:
    assert _rewrite("helper(this, x)", implicit_name="this") == "this._helper(x)"


def test_method_call_rewriter_qualifies_staticmethods_by_class_name() -> None:
    assert _rewrite("helper(x)", method_kind="staticmethod", class_name="Owner") == (
        "Owner._helper(x)"
    )


def test_method_call_rewriter_leaves_staticmethods_without_a_class_name_alone() -> None:
    assert _rewrite("helper(x)", method_kind="staticmethod", class_name=None) == "helper(x)"


def test_method_call_rewriter_leaves_calls_alone_without_a_method_kind() -> None:
    assert _rewrite("helper(self, x)", method_kind=None) == "helper(self, x)"


def test_method_call_rewriter_ignores_other_callees_and_attribute_calls() -> None:
    assert _rewrite("other(self, x)") == "other(self, x)"
    assert _rewrite("obj.helper(self, x)") == "obj.helper(self, x)"


def test_method_call_rewriter_rewrites_nested_calls_inside_arguments() -> None:
    assert _rewrite("use(helper(self, helper(self, x)))") == "use(self._helper(self._helper(x)))"


# ---------------------------------------------------------------------------
# LoopReturnFinder


def _has_loop_return(source: str) -> bool:
    finder = LoopReturnFinder()
    finder.visit(_parse(source))
    return finder.has_loop_return


@pytest.mark.parametrize(
    "source",
    [
        "for x in xs:\n    return x\n",
        "while cond():\n    return 1\n",
        "for x in xs:\n    if x:\n        return x\n",
        "for x in xs:\n    for y in x:\n        return y\n",
        "while True:\n    for y in ys:\n        pass\n    return 1\n",
    ],
)
def test_loop_return_finder_detects_returns_inside_loops(source: str) -> None:
    assert _has_loop_return(source)


@pytest.mark.parametrize(
    "source",
    [
        "return 1\n",
        "for x in xs:\n    pass\nreturn 1\n",
        "if cond():\n    return 1\n",
        "for x in xs:\n    def inner():\n        return x\n",
        "for x in xs:\n    async def inner():\n        return x\n",
        "def inner():\n    for x in xs:\n        return x\n",
    ],
)
def test_loop_return_finder_ignores_returns_outside_loops_and_in_nested_functions(
    source: str,
) -> None:
    assert not _has_loop_return(source)


# ---------------------------------------------------------------------------
# NameCollector


def _used_names(source: str) -> set[str]:
    collector = NameCollector()
    collector.visit(_parse(source))
    return collector.used


def test_name_collector_collects_loads_and_ignores_stores() -> None:
    assert _used_names("x = y + z\nw = x\n") == {"y", "z", "x"}


def test_name_collector_reads_attribute_bases_and_call_names() -> None:
    assert _used_names("obj.attr(arg)\n") == {"obj", "arg"}


def test_name_collector_skips_annotations_but_reads_values() -> None:
    assert _used_names("x: SomeType = value\n") == {"value"}
    assert _used_names("x: SomeType\n") == set()


def test_name_collector_stops_at_nested_function_boundaries() -> None:
    assert (
        _used_names("def inner():\n    return hidden\nasync def more():\n    return also\n")
        == set()
    )


def test_name_collector_enters_comprehensions_and_compound_statements() -> None:
    assert _used_names("if flag:\n    out = [f(i) for i in items]\n") == {"flag", "f", "i", "items"}


# ---------------------------------------------------------------------------
# AugAssignFinder


def _aug_targets(source: str) -> set[str]:
    finder = AugAssignFinder()
    finder.visit(_parse(source))
    return finder.aug_assign_targets


def test_aug_assign_finder_records_simple_name_targets() -> None:
    assert _aug_targets("x += 1\ny -= 2\nz = 3\n") == {"x", "y"}


def test_aug_assign_finder_ignores_attribute_and_subscript_targets() -> None:
    assert _aug_targets("obj.count += 1\nvalues[i] *= 2\n") == set()


def test_aug_assign_finder_enters_compound_statements_but_not_nested_functions() -> None:
    assert _aug_targets("for i in xs:\n    total += i\ndef inner():\n    hidden += 1\n") == {
        "total"
    }


# ---------------------------------------------------------------------------
# AssignTargetVisitor


def _assignments(source: str) -> AssignTargetVisitor:
    visitor = AssignTargetVisitor()
    visitor.visit(_parse(source))
    return visitor


def test_assign_target_visitor_records_simple_name_targets_of_every_assignment_kind() -> None:
    visitor = _assignments("a = 1\nb = c = 2\nd += 3\ne: int = 4\nf: int\n")
    assert visitor.assigned_names == {"a", "b", "c", "d", "e", "f"}


def test_assign_target_visitor_ignores_destructuring_attribute_and_subscript_targets() -> None:
    visitor = _assignments("a, b = pair\nobj.attr = 1\nvalues[0] = 2\n")
    assert visitor.assigned_names == set()


def test_assign_target_visitor_records_global_and_nonlocal_declarations() -> None:
    visitor = _assignments("global g1, g2\nnonlocal n1\ng1 = 1\n")
    assert visitor.declared_global_in_block == {"g1", "g2"}
    assert visitor.declared_nonlocal_in_block == {"n1"}
    assert visitor.assigned_names == {"g1"}


def test_assign_target_visitor_enters_compound_statements_but_not_nested_functions() -> None:
    visitor = _assignments(
        "if flag:\n"
        "    inside = 1\n"
        "def inner():\n"
        "    global hidden\n"
        "    hidden = 2\n"
        "async def more():\n"
        "    other = 3\n"
    )
    assert visitor.assigned_names == {"inside"}
    assert visitor.declared_global_in_block == set()


# ---------------------------------------------------------------------------
# ClassLocator


def _locate_class(source: str, name: str) -> ClassLocator:
    locator = ClassLocator(source, name)
    locator.visit(_parse(source))
    return locator


def test_class_locator_returns_the_last_line_and_body_indent_of_a_module_level_class() -> None:
    source = "class Target:\n    x = 1\n\n    def method(self):\n        pass\n\nclass Other:\n    pass\n"
    locator = _locate_class(source, "Target")
    assert locator.matches == 1
    # ``end_lineno - 1``: the line before the class ends, in 1-based terms.
    assert locator.result == (4, "    ")


def test_class_locator_reports_no_result_when_the_class_is_absent() -> None:
    locator = _locate_class("class Other:\n    pass\n", "Target")
    assert locator.matches == 0
    assert locator.result is None


def test_class_locator_rejects_a_class_nested_in_a_function() -> None:
    locator = _locate_class("def make():\n    class Target:\n        pass\n", "Target")
    assert locator.matches == 1
    assert locator.result is None


def test_class_locator_rejects_a_class_nested_in_another_class() -> None:
    locator = _locate_class("class Outer:\n    class Target:\n        pass\n", "Target")
    assert locator.result is None


def test_class_locator_rejects_duplicate_module_level_classes() -> None:
    source = "class Target:\n    a = 1\n\nclass Target:\n    b = 2\n"
    locator = _locate_class(source, "Target")
    assert locator.matches == 2
    assert locator.result is None


def test_class_locator_uses_the_indent_of_the_first_body_statement() -> None:
    source = "class Target:\n\tvalue = 1\n"
    assert _locate_class(source, "Target").result == (1, "\t")


# ---------------------------------------------------------------------------
# FuncLocator


def _locate_function(source: str, name: str) -> Optional[Tuple[int, str]]:
    locator = FuncLocator(source, name)
    locator.visit(_parse(source))
    return locator.result


def test_func_locator_inserts_before_the_first_executable_statement() -> None:
    source = "def target():\n    x = 1\n    return x\n"
    assert _locate_function(source, "target") == (1, "")


def test_func_locator_skips_the_docstring() -> None:
    source = 'def target():\n    """Doc."""\n    x = 1\n    return x\n'
    assert _locate_function(source, "target") == (2, "")


def test_func_locator_inserts_after_leading_nested_definitions() -> None:
    source = (
        "def target():\n"
        "    def first():\n"
        "        pass\n"
        "    class Local:\n"
        "        pass\n"
        "    return first()\n"
    )
    assert _locate_function(source, "target") == (5, "")


def test_func_locator_inserts_after_the_last_definition_when_nothing_else_follows() -> None:
    source = "def target():\n    def first():\n        pass\n    def second():\n        pass\n"
    assert _locate_function(source, "target") == (5, "")


def test_func_locator_falls_back_to_the_def_line_for_a_docstring_only_body() -> None:
    source = 'def target():\n    """Only a docstring."""\n'
    assert _locate_function(source, "target") == (1, "")


def test_func_locator_finds_methods_and_async_functions_with_their_indent() -> None:
    source = "class C:\n    async def target(self):\n        await self.go()\n"
    assert _locate_function(source, "target") == (2, "    ")


def test_func_locator_returns_none_for_an_unknown_function() -> None:
    assert _locate_function("def other():\n    pass\n", "target") is None
