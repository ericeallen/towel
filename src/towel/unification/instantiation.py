"""Verify that a helper call reproduces the block it replaces.

Unification, parameter substitution, and hygienic renaming each transform
syntax by their own rules. Any disagreement between them silently changes the
program: a name parameterized at one position but substituted at every
position, a renamed variable whose caller still uses the old name, a thunk
passed where a value is read. This module checks the one invariant that
subsumes all of those rules: instantiating the helper body with a block's
actual arguments must reproduce that block exactly.
"""

from __future__ import annotations

import ast
import copy
from typing import Dict, List, Mapping, Optional, Sequence, cast

from .semantic_safety import bound_names


class InstantiationError(Exception):
    """The helper body cannot be reduced against this call."""


def instantiation_mismatch(
    helper: ast.FunctionDef,
    call_statement: ast.stmt,
    block: Sequence[ast.AST],
    template_renames: Mapping[str, str],
    block_renames: Mapping[str, str],
    *,
    preamble_length: int,
    returns_variables: bool,
) -> Optional[str]:
    """Return why ``helper`` applied to ``call_statement`` differs from ``block``.

    The helper body is built from the template block, so it may spell a bound
    variable by the template's name or by the canonical alpha-renamed name.
    ``template_renames`` and ``block_renames`` each map a block's original
    names to those canonical names; together they translate both spellings to
    this block's own names. ``preamble_length`` counts injected global/nonlocal
    declarations at the start of the body and ``returns_variables`` states
    whether the extractor appended a return of the block's live variables.
    """
    call = _extract_call(call_statement, helper.name)
    if call is None:
        return "call shape"
    parameters = [argument.arg for argument in helper.args.args]
    if (
        helper.args.posonlyargs
        or helper.args.kwonlyargs
        or helper.args.vararg
        or helper.args.kwarg
        or len(parameters) != len(call.args)
        or call.keywords
    ):
        return "arity"
    end = len(helper.body) - (1 if returns_variables else 0)
    body = [copy.deepcopy(statement) for statement in helper.body[preamble_length:end]]
    inverse = _spellings_to_block_names(template_renames, block_renames)
    shape = _statement_shape_mismatch(helper, call_statement, body, inverse, returns_variables)
    if shape is not None:
        return shape
    arguments = dict(zip(parameters, call.args))
    try:
        reduced = [cast(ast.stmt, _Reducer(arguments).visit(statement)) for statement in body]
    except InstantiationError as error:
        return str(error)
    restored = [_IdentifierRenamer(inverse).visit(statement) for statement in reduced]
    expected = _alpha_normalize(
        ast.Module(body=[cast(ast.stmt, copy.deepcopy(node)) for node in block], type_ignores=[])
    )
    actual = _alpha_normalize(ast.Module(body=restored, type_ignores=[]))
    if ast.dump(actual, include_attributes=False) != ast.dump(expected, include_attributes=False):
        return f"body: {ast.unparse(actual)!r} != {ast.unparse(expected)!r}"
    return None


def _alpha_normalize(module: ast.Module) -> ast.Module:
    """Rename block-bound names in first-occurrence order; free names stay.

    Loop targets, comprehension variables and other binders may be spelled
    differently in the helper and in a block without changing meaning. Names
    the block never binds are left alone, so a helper binder that captures a
    block's free name still compares unequal.
    """
    bound = bound_names(module.body)
    order: Dict[str, str] = {}
    for node in ast.walk(module):
        for name in _identifiers(node):
            if name in bound and name not in order:
                order[name] = f"__alpha_{len(order)}"
    return cast(ast.Module, _IdentifierRenamer(order).visit(module))


def _identifiers(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return [node.name] if node.name else []
    if isinstance(node, ast.MatchMapping):
        return [node.rest] if node.rest else []
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return list(node.names)
    if isinstance(node, ast.alias):
        return [node.asname or node.name.split(".")[0]]
    return []


def _spellings_to_block_names(
    template_renames: Mapping[str, str], block_renames: Mapping[str, str]
) -> Dict[str, str]:
    """Map canonical and template spellings of renamed names to this block's names."""
    by_canonical = {canonical: original for original, canonical in block_renames.items()}
    mapping = dict(by_canonical)
    for template_name, canonical in template_renames.items():
        if canonical in by_canonical:
            mapping[template_name] = by_canonical[canonical]
    return mapping


def _statement_shape_mismatch(
    helper: ast.FunctionDef,
    call_statement: ast.stmt,
    body: Sequence[ast.stmt],
    inverse: Mapping[str, str],
    returns_variables: bool,
) -> Optional[str]:
    """Check that the call statement carries the helper's result the same way.

    A helper that returns early must be called by `return helper(...)`; a
    helper that returns live variables must be called by an assignment whose
    targets are those variables, in order, under the block's own spelling.
    """
    own_returns = [
        node
        for statement in body
        for node in _walk_own_scope(statement)
        if isinstance(node, ast.Return)
    ]
    if returns_variables:
        if own_returns:
            return "early return alongside returned variables"
        returned = helper.body[-1]
        if not isinstance(returned, ast.Return) or returned.value is None:
            return "missing variable return"
        value = returned.value
        elements = list(value.elts) if isinstance(value, ast.Tuple) else [value]
        if not all(isinstance(element, ast.Name) for element in elements):
            return "variable return shape"
        expected = [
            inverse.get(cast(ast.Name, element).id, cast(ast.Name, element).id)
            for element in elements
        ]
        if not isinstance(call_statement, ast.Assign) or len(call_statement.targets) != 1:
            return "assignment shape"
        target = call_statement.targets[0]
        targets = list(target.elts) if isinstance(target, ast.Tuple) else [target]
        if not all(isinstance(item, ast.Name) for item in targets):
            return "assignment target shape"
        if [cast(ast.Name, item).id for item in targets] != expected:
            return (
                f"assignment targets {[cast(ast.Name, item).id for item in targets]} != {expected}"
            )
        return None
    if own_returns and not isinstance(call_statement, ast.Return):
        return "early return without return call"
    return None


def _walk_own_scope(node: ast.AST) -> List[ast.AST]:
    """Nodes of ``node`` without entering nested function or class scopes."""
    found: List[ast.AST] = []
    pending = [node]
    while pending:
        current = pending.pop()
        found.append(current)
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(current))
    return found


def _extract_call(statement: ast.stmt, helper_name: str) -> Optional[ast.Call]:
    value = getattr(statement, "value", None)
    if isinstance(statement, (ast.Expr, ast.Assign, ast.Return)) and isinstance(value, ast.Call):
        if isinstance(value.func, ast.Name) and value.func.id == helper_name:
            return value
    return None


class _Reducer(ast.NodeTransformer):
    """Replace parameter names by arguments, beta-reducing thunk calls."""

    def __init__(self, arguments: Mapping[str, ast.expr]) -> None:
        self._arguments = arguments

    def visit_Call(self, node: ast.Call) -> ast.AST:
        function = node.func
        if isinstance(function, ast.Name) and function.id in self._arguments:
            argument = self._arguments[function.id]
            if isinstance(argument, ast.Lambda):
                actual_args = [self.visit(item) for item in node.args]
                actual_keywords = [self.visit(item) for item in node.keywords]
                return _beta_reduce(argument, actual_args, actual_keywords)
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self._arguments:
            return copy.deepcopy(self._arguments[node.id])
        return node


def _beta_reduce(
    thunk: ast.Lambda, actual_args: List[ast.expr], actual_keywords: List[ast.keyword]
) -> ast.AST:
    signature = thunk.args
    forwarding = (
        signature.vararg is not None
        and signature.kwarg is not None
        and not signature.args
        and not signature.posonlyargs
        and not signature.kwonlyargs
    )
    if forwarding:
        body = thunk.body
        if not (
            isinstance(body, ast.Call)
            and len(body.args) == 1
            and isinstance(body.args[0], ast.Starred)
            and isinstance(body.args[0].value, ast.Name)
            and signature.vararg is not None
            and body.args[0].value.id == signature.vararg.arg
            and len(body.keywords) == 1
            and body.keywords[0].arg is None
            and isinstance(body.keywords[0].value, ast.Name)
            and signature.kwarg is not None
            and body.keywords[0].value.id == signature.kwarg.arg
        ):
            raise InstantiationError("forwarding thunk shape")
        return ast.Call(func=copy.deepcopy(body.func), args=actual_args, keywords=actual_keywords)
    if (
        actual_keywords
        or signature.posonlyargs
        or signature.kwonlyargs
        or signature.vararg
        or signature.kwarg
        or signature.defaults
        or len(signature.args) != len(actual_args)
    ):
        raise InstantiationError("thunk arity")
    bindings: Dict[str, ast.expr] = {
        parameter.arg: actual for parameter, actual in zip(signature.args, actual_args)
    }
    return cast(ast.AST, _Reducer(bindings).visit(copy.deepcopy(thunk.body)))


class _IdentifierRenamer(ast.NodeTransformer):
    """Rename every identifier position that hygienic renaming can touch."""

    def __init__(self, mapping: Mapping[str, str]) -> None:
        self._mapping = mapping

    def _rename(self, name: Optional[str]) -> Optional[str]:
        return self._mapping.get(name, name) if name is not None else None

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = self._mapping.get(node.id, node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = self._mapping.get(node.arg, node.arg)
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = self._mapping.get(node.name, node.name)
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        node.name = self._mapping.get(node.name, node.name)
        return self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        node.name = self._mapping.get(node.name, node.name)
        return self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        node.name = self._rename(node.name)
        return self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> ast.AST:
        node.names = [self._mapping.get(name, name) for name in node.names]
        return node

    def visit_Nonlocal(self, node: ast.Nonlocal) -> ast.AST:
        node.names = [self._mapping.get(name, name) for name in node.names]
        return node

    def visit_MatchAs(self, node: ast.MatchAs) -> ast.AST:
        node.name = self._rename(node.name)
        return self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> ast.AST:
        node.name = self._rename(node.name)
        return node

    def visit_MatchMapping(self, node: ast.MatchMapping) -> ast.AST:
        node.rest = self._rename(node.rest)
        return self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> ast.AST:
        if node.asname is not None:
            node.asname = self._mapping.get(node.asname, node.asname)
        elif "." not in node.name:
            node.name = self._mapping.get(node.name, node.name)
        return node
