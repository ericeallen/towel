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
from weakref import WeakKeyDictionary
from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple, cast

from .bounded_cache import BoundedCache
from .semantic_safety import bound_names, walk_own_scope
from .visitors import visit_as
from .structural_memo import structural_id

_EXPECTED_DUMPS: BoundedCache[str, str] = BoundedCache(16_384)
"""The alpha-normalized dump of a block, by structural id.

A block is checked against every call that reproduces it, once per pair it
forms, and its normalized form is a function of its structure alone: the
same code re-parsed after a rewrite hits too. Per process; the workers fork
after parsing and each keeps its own copy.
"""


class InstantiationError(Exception):
    """The helper body cannot be reduced against this call."""


def instantiation_mismatch(
    helper: ast.FunctionDef,
    call_statement: ast.stmt,
    block: Sequence[ast.stmt],
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
    key = (
        _helper_dump(helper),
        ast.dump(call_statement, include_attributes=False),
        structural_id(block),
        tuple(sorted(template_renames.items())),
        tuple(sorted(block_renames.items())),
        preamble_length,
        returns_variables,
    )
    known = _VERDICTS.get(key)
    if known is not None:
        return known.verdict
    verdict = _instantiation_mismatch(
        helper,
        call_statement,
        block,
        template_renames,
        block_renames,
        preamble_length=preamble_length,
        returns_variables=returns_variables,
    )
    _VERDICTS.put(key, _Verdict(verdict))
    return verdict


class _Verdict(NamedTuple):
    """A memoized verdict; the wrapper lets ``None`` (no mismatch) be a cache hit."""

    verdict: Optional[str]


_VERDICTS: BoundedCache[
    Tuple[str, str, str, Tuple[Tuple[str, str], ...], Tuple[Tuple[str, str], ...], int, bool],
    _Verdict,
] = BoundedCache(65_536)
"""Verdicts by everything the check depends on.

Every pair renders a helper and checks it against each of its blocks, and
the same helper meets the same block through every pair the block forms;
on fifty near-identical functions the check ran 12,200 times for 101
distinct inputs. The helper's dump is memoized per node below, the block's
form by structure, so a re-parse hits too.
"""

_HELPER_DUMPS: "WeakKeyDictionary[ast.FunctionDef, str]" = WeakKeyDictionary()


def _helper_dump(helper: ast.FunctionDef) -> str:
    known = _HELPER_DUMPS.get(helper)
    if known is None:
        known = ast.dump(helper, include_attributes=False)
        _HELPER_DUMPS[helper] = known
    return known


def _instantiation_mismatch(
    helper: ast.FunctionDef,
    call_statement: ast.stmt,
    block: Sequence[ast.stmt],
    template_renames: Mapping[str, str],
    block_renames: Mapping[str, str],
    *,
    preamble_length: int,
    returns_variables: bool,
) -> Optional[str]:
    """See ``instantiation_mismatch``; this computes it."""
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
        reduced = [visit_as(_Reducer(arguments), statement) for statement in body]
    except InstantiationError as error:
        return str(error)
    restored = [_IdentifierRenamer(inverse).visit(statement) for statement in reduced]
    actual = _alpha_normalize(ast.Module(body=restored, type_ignores=[]))
    if _normalized_dump(actual) != _expected_dump(block):
        return f"body: {ast.unparse(actual)!r} != {ast.unparse(_expected_form(block))!r}"
    return None


def _expected_form(block: Sequence[ast.stmt]) -> ast.Module:
    """The block as the reduced helper body must read, on a copy."""
    return _alpha_normalize(
        ast.Module(body=[copy.deepcopy(node) for node in block], type_ignores=[])
    )


def _normalized_dump(module: ast.Module) -> str:
    return ast.dump(module, include_attributes=False)


def _expected_dump(block: Sequence[ast.stmt]) -> str:
    """``_normalized_dump(_expected_form(block))``, memoized on the block's structure."""
    key = structural_id(block)
    cached = _EXPECTED_DUMPS.get(key)
    if cached is None:
        cached = _EXPECTED_DUMPS.put(key, _normalized_dump(_expected_form(block)))
    return cached


def _alpha_normalize(module: ast.Module) -> ast.Module:
    """Rename block-bound names in first-occurrence order; free names stay.

    Loop targets, comprehension variables and other binders may be spelled
    differently in the helper and in a block without changing meaning. Names
    the block never binds are left alone, so a helper binder that captures a
    block's free name still compares unequal.
    """
    # Annotations inside a function body are never evaluated; the helper
    # keeps the template's, so they take no part in the comparison.
    for node in ast.walk(module):
        if isinstance(node, ast.AnnAssign):
            node.annotation = ast.Name(id="__annotation__", ctx=ast.Load())
    # A lambda's parameters are visible only inside it; they are renamed
    # there, by position, before the block-level binders are.
    module = visit_as(_LambdaBinderRenamer(), module)
    bound = bound_names(module.body) - _fixed_import_names(module.body)
    order: Dict[str, str] = {}
    for node in ast.walk(module):
        for name in _identifiers(node):
            if name in bound and name not in order:
                order[name] = f"__alpha_{len(order)}"
    return visit_as(_IdentifierRenamer(order), module)


def _fixed_import_names(statements: Sequence[ast.stmt]) -> Set[str]:
    """Names an import binds without ``as``: they name what is imported and cannot be renamed."""
    names: Set[str] = set()
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.asname is None and alias.name != "*":
                        names.add(alias.name.split(".")[0])
    return names


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
        return [node.asname] if node.asname is not None else []
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
        for node in walk_own_scope(statement)
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
    """Apply a thunk lambda to the call's arguments and return its reduced body.

    A forwarding thunk -- ``lambda *a, **k: f(*a, **k)`` -- reduces to the wrapped
    call ``f`` applied to the actual arguments; any other shape of forwarding
    lambda is rejected (``forwarding thunk shape``) because it cannot be reduced
    soundly. A plain thunk has its parameters bound to the actual arguments,
    after an arity check, and the substituted body is returned. This is the
    beta-reduction step that lets instantiation compare a helper call against the
    original block up to the thunks the extractor introduced.
    """
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
    return visit_as(_Reducer(bindings), copy.deepcopy(thunk.body))


class _LambdaBinderRenamer(ast.NodeTransformer):
    """Rename each lambda's parameters to positional names, scoped to that lambda.

    ``lambda value: value * 2`` and ``lambda other: other * 2`` are the same
    function. A parameter is renamed inside its own lambda and nowhere else,
    so a free name spelled the same outside the lambda is untouched, and an
    inner lambda that rebinds a name shadows the outer renaming. Lambdas are
    numbered in visiting order, so a helper and a block of the same shape
    receive the same names. Defaults evaluate in the enclosing scope and are
    visited under it.
    """

    def __init__(self) -> None:
        self._scopes: List[Dict[str, str]] = []
        self._count = 0

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        index = self._count
        self._count += 1
        arguments = node.args
        arguments.defaults = [visit_as(self, default) for default in arguments.defaults]
        arguments.kw_defaults = [
            visit_as(self, default) if default is not None else None
            for default in arguments.kw_defaults
        ]
        parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        if arguments.vararg is not None:
            parameters.append(arguments.vararg)
        if arguments.kwarg is not None:
            parameters.append(arguments.kwarg)
        mapping = dict(self._scopes[-1]) if self._scopes else {}
        for position, parameter in enumerate(parameters):
            mapping[parameter.arg] = f"__lambda_{index}_{position}"
            parameter.arg = mapping[parameter.arg]
        self._scopes.append(mapping)
        node.body = visit_as(self, node.body)
        self._scopes.pop()
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if self._scopes:
            node.id = self._scopes[-1].get(node.id, node.id)
        return node


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
        # ``import a as b`` binds a free name b; ``from m import x`` binds the
        # name of what it imports, which no renaming may touch.
        if node.asname is not None:
            node.asname = self._mapping.get(node.asname, node.asname)
        return node
