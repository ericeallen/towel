"""Pass a thunk's expression eagerly when doing so is unobservable.

A thunk parameter is evaluated inside the helper at the original position.
When the helper evaluates that thunk before any other effect, exactly once,
and on every path, evaluating the expression at the call site instead
changes nothing an observer can see: the call site's own arguments are
names, literals, tuples of those, lambda creations, or earlier such thunks
in the same order. This pass detects that situation from the helper body
and turns the thunk back into an ordinary argument, so the helper reads
``__param_0`` rather than ``__param_0()``.
"""

from __future__ import annotations

import ast
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from .unifier import Substitution


def inline_leading_thunks(
    helper: ast.FunctionDef, substitution: Substitution, param_order: Dict[str, int]
) -> Set[str]:
    """Inline thunks that the helper evaluates first, once, unconditionally.

    Thunks are considered in the helper's evaluation order and must appear in
    increasing parameter position, because call-site arguments are evaluated
    left to right. Returns the inlined parameter names; ``helper`` and
    ``substitution`` are updated in place.
    """
    # A thunk is a function parameter with no bound variables. The extractor
    # also lists it under ``params_used_as_callee`` because the body calls it,
    # so that set cannot distinguish thunks from forwarded callees here.
    candidates = {name for name, bound in substitution.function_params.items() if not bound}
    if not candidates:
        return set()
    inlined: List[str] = []
    for statement in helper.body:
        if isinstance(statement, (ast.Global, ast.Nonlocal)):
            continue
        nodes, complete = _evaluation_prefix(statement)
        for node in nodes:
            thunk = _thunk_name(node, candidates)
            if thunk is not None:
                if thunk in inlined or not _single_use(helper, thunk):
                    return _apply(helper, substitution, inlined)
                if inlined and param_order[thunk] <= param_order[inlined[-1]]:
                    return _apply(helper, substitution, inlined)
                inlined.append(thunk)
                continue
            if not _effect_free(node):
                return _apply(helper, substitution, inlined)
        if not complete:
            break
    return _apply(helper, substitution, inlined)


def _apply(helper: ast.FunctionDef, substitution: Substitution, names: Sequence[str]) -> Set[str]:
    for name in names:
        del substitution.function_params[name]
        substitution.params_used_as_callee.discard(name)
        substitution.inlined_parameters.add(name)
    if names:
        _ThunkCallRemover(set(names)).visit(helper)
    return set(names)


def _thunk_name(node: ast.AST, candidates: Set[str]) -> Optional[str]:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in candidates
        and not node.args
        and not node.keywords
    ):
        return node.func.id
    return None


def _single_use(helper: ast.FunctionDef, name: str) -> bool:
    return (
        sum(
            1
            for statement in helper.body
            for node in ast.walk(statement)
            if isinstance(node, ast.Name) and node.id == name
        )
        == 1
    )


def _effect_free(node: ast.AST) -> bool:
    """Whether evaluating this node, given its already-evaluated children, has no effect.

    Displays and lambda creation allocate but do not run user code. An
    attribute of a string or number literal resolves on a builtin type.
    """
    if isinstance(node, (ast.Name, ast.Constant, ast.Tuple, ast.List, ast.Set, ast.Dict)):
        return True
    if isinstance(node, ast.Lambda):
        return True
    if isinstance(node, ast.Attribute):
        return isinstance(node.value, ast.Constant)
    return False


class _ThunkCallRemover(ast.NodeTransformer):
    def __init__(self, names: Set[str]) -> None:
        self._names = names

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if isinstance(node.func, ast.Name) and node.func.id in self._names and not node.args:
            return ast.copy_location(ast.Name(id=node.func.id, ctx=ast.Load()), node)
        return self.generic_visit(node)


def _evaluation_prefix(statement: ast.stmt) -> Tuple[List[ast.AST], bool]:
    """Nodes a statement evaluates unconditionally and exactly once, in order.

    The flag is True when the whole statement is such a prefix, so the next
    statement is also reached unconditionally. Compound statements yield
    only the part evaluated before any branch, loop, or exception context.
    """
    nodes: List[ast.AST] = []
    try:
        if isinstance(statement, (ast.Expr, ast.Return)):
            if statement.value is not None:
                nodes.extend(_expression_order(statement.value))
            return nodes, True
        if isinstance(statement, ast.Assign):
            nodes.extend(_expression_order(statement.value))
            for target in statement.targets:
                nodes.extend(_target_order(target))
            return nodes, True
        if isinstance(statement, ast.AnnAssign):
            if statement.value is not None:
                nodes.extend(_expression_order(statement.value))
            nodes.extend(_target_order(statement.target))
            return nodes, True
        if isinstance(statement, ast.AugAssign):
            nodes.extend(_target_order(statement.target))
            nodes.extend(_expression_order(statement.value))
            nodes.append(statement)  # the in-place operation itself
            return nodes, True
        if isinstance(statement, ast.Delete):
            for target in statement.targets:
                nodes.extend(_target_order(target))
            return nodes, True
        if isinstance(statement, ast.Raise):
            if statement.exc is not None:
                nodes.extend(_expression_order(statement.exc))
            if statement.cause is not None:
                nodes.extend(_expression_order(statement.cause))
            return nodes, True
        if isinstance(statement, ast.If):
            nodes.extend(_expression_order(statement.test))
        elif isinstance(statement, (ast.For, ast.AsyncFor)):
            nodes.extend(_expression_order(statement.iter))
        elif isinstance(statement, (ast.With, ast.AsyncWith)) and statement.items:
            nodes.extend(_expression_order(statement.items[0].context_expr))
        elif isinstance(statement, ast.Match):
            nodes.extend(_expression_order(statement.subject))
        # While re-evaluates its test; Try routes exceptions; Assert depends on -O.
        return nodes, False
    except _Stop as stop:
        nodes.extend(stop.nodes)
        return nodes, False


class _Stop(Exception):
    """Evaluation reached a conditional, repeated, or deferred region."""

    def __init__(self, nodes: List[ast.AST]) -> None:
        super().__init__()
        self.nodes = nodes


def _expression_order(expression: ast.AST) -> List[ast.AST]:
    collected: List[ast.AST] = []
    try:
        for node in _walk(expression):
            collected.append(node)
    except _Stop:
        raise _Stop(collected)
    return collected


def _target_order(target: ast.AST) -> List[ast.AST]:
    if isinstance(target, ast.Name):
        return []
    if isinstance(target, (ast.Tuple, ast.List)):
        nodes: List[ast.AST] = []
        for element in target.elts:
            nodes.extend(_target_order(element))
        return nodes
    if isinstance(target, ast.Starred):
        return _target_order(target.value)
    if isinstance(target, ast.Attribute):
        return _expression_order(target.value) + [target]
    if isinstance(target, ast.Subscript):
        return _expression_order(target.value) + _expression_order(target.slice) + [target]
    return [target]


def _walk(node: ast.AST) -> Iterator[ast.AST]:
    """Yield nodes in CPython evaluation order until a region that may not run once."""
    if isinstance(node, (ast.Name, ast.Constant, ast.Lambda)):
        yield node
    elif isinstance(node, ast.Attribute):
        yield from _walk(node.value)
        yield node
    elif isinstance(node, ast.Subscript):
        yield from _walk(node.value)
        yield from _walk(node.slice)
        yield node
    elif isinstance(node, ast.Slice):
        for part in (node.lower, node.upper, node.step):
            if part is not None:
                yield from _walk(part)
    elif isinstance(node, ast.Call):
        yield from _walk(node.func)
        for argument in node.args:
            yield from _walk(argument)
        for keyword in node.keywords:
            yield from _walk(keyword.value)
        yield node
    elif isinstance(node, ast.Starred):
        yield from _walk(node.value)
    elif isinstance(node, ast.BinOp):
        yield from _walk(node.left)
        yield from _walk(node.right)
        yield node
    elif isinstance(node, ast.UnaryOp):
        yield from _walk(node.operand)
        yield node
    elif isinstance(node, ast.BoolOp):
        yield from _walk(node.values[0])
        raise _Stop([])
    elif isinstance(node, ast.IfExp):
        yield from _walk(node.test)
        raise _Stop([])
    elif isinstance(node, ast.Compare):
        yield from _walk(node.left)
        yield from _walk(node.comparators[0])
        yield node
        if len(node.comparators) > 1:
            raise _Stop([])
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for element in node.elts:
            yield from _walk(element)
        yield node
    elif isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if key is not None:
                yield from _walk(key)
            yield from _walk(value)
        yield node
    elif isinstance(node, ast.JoinedStr):
        for value in node.values:
            yield from _walk(value)
        yield node
    elif isinstance(node, ast.FormattedValue):
        yield from _walk(node.value)
        yield node
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        yield from _walk(node.generators[0].iter)
        raise _Stop([])
    elif isinstance(node, ast.NamedExpr):
        yield from _walk(node.value)
        yield node
    else:
        # Await, Yield, and anything unmodeled: treat as an effect that ends the prefix.
        yield node
