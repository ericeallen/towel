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
actual arguments must reproduce that block exactly, up to the spelling of
the names the block binds, and only where that spelling cannot be seen
while the program runs (``observable_renamings``).
"""

from __future__ import annotations

import ast
import copy
from weakref import WeakKeyDictionary
from functools import reduce
from typing import (
    AbstractSet,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
    cast,
)

from ..canonical_ast import canonical_dump
from .bounded_cache import BoundedCache
from .parameters import parameter_names
from .scope_analyzer import pattern_expressions
from .semantic_safety import bound_names, walk_own_scope
from .statement_facts import bindings_of, import_binding_names, pattern_capture_names
from .visitors import (
    annotation_expressions,
    evaluated_before_definition,
    visit_as,
    visit_comprehension_result,
)
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
        canonical_dump(call_statement),
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
        known = canonical_dump(helper)
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
    # The binders as the helper spells them when it runs this block.
    helper_binders = _binder_sequence(reduced)
    restored = [_IdentifierRenamer(inverse).visit(statement) for statement in reduced]
    actual = _alpha_normalize(ast.Module(body=restored, type_ignores=[]))
    if _normalized_dump(actual) != _expected_dump(block):
        return f"body: {ast.unparse(actual)!r} != {ast.unparse(_expected_form(block))!r}"
    block_binders = _binder_sequence(block)
    if len(helper_binders) != len(block_binders):
        return "binder correspondence"
    renamed = {own for spelled, own in zip(helper_binders, block_binders) if spelled != own}
    observable = observable_renamings(block, renamed)
    if observable:
        return f"renamed binder observable: {', '.join(sorted(observable))}"
    return None


def _expected_form(block: Sequence[ast.stmt]) -> ast.Module:
    """The block as the reduced helper body must read, on a copy."""
    return _alpha_normalize(
        ast.Module(body=[copy.deepcopy(node) for node in block], type_ignores=[])
    )


def _normalized_dump(module: ast.Module) -> str:
    return canonical_dump(module)


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
    differently in the helper and in a block without changing the value
    computed; whether the difference can be seen by other means is
    ``observable_renamings``'s question. Names the block never binds are
    left alone, so a helper binder that captures a block's free name still
    compares unequal.
    """
    module, order = _alpha_order(module)
    return visit_as(_IdentifierRenamer(order), module)


def _alpha_order(module: ast.Module) -> Tuple[ast.Module, Dict[str, str]]:
    """The module prepared for comparison, and its binders' canonical names in first-occurrence order.

    Mutates ``module``: annotations are blanked and lambda parameters renamed.
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
    return module, order


def _binder_sequence(statements: Sequence[ast.stmt]) -> List[str]:
    """The binders of ``statements`` in the order ``_alpha_normalize`` numbers them, on a copy.

    Two bodies whose normalized forms agree bind their ``i``-th names at the
    same places, so pairing the sequences by position pairs each helper
    spelling with the block's own.
    """
    copied = ast.Module(
        body=[copy.deepcopy(statement) for statement in statements], type_ignores=[]
    )
    return list(_alpha_order(copied)[1])


def observable_renamings(block: Sequence[ast.stmt], renamed: AbstractSet[str]) -> FrozenSet[str]:
    """The binders among ``renamed`` whose spelling the running block could observe.

    The helper runs a site's block with the template's spelling of the names
    the block binds. A local's name reaches the program in exactly these ways:

    - ``UnboundLocalError`` names it when a read, an augmented assignment or
      a ``del`` finds it unbound, and ``NameError`` names it when a closure
      reads its empty cell. Each binder read where it may be unbound (after
      an untaken branch, an empty loop, an unmatched case, a ``del``, the end
      of the ``except ... as`` clause that bound it), or read by a nested
      scope that may run while it is unbound, is observable.
    - A ``global`` or ``nonlocal`` declaration makes the name another
      scope's variable, not a spelling: renaming it writes a different one.
    - A frame (``locals()``, ``vars()``, ``dir()``, ``sys._getframe()``,
      ``eval``) lists its locals by name; such blocks, and functions that
      read their frame outside the block, are declined before this is asked
      (``requires_original_frame``, ``frame_read_outside_block``).
    - A ``def`` or ``class`` stores its name in the object it creates, and a
      lambda's parameter names are its signature; a block whose created
      objects can be seen other than by calling them where they stand is
      declined before this is asked (``created_object_escapes``).

    When every read of every renamed binder is definitely bound, and none is
    declared, the renaming is unobservable. Whether a read is definitely
    bound follows Python's evaluation order: assignment expressions bind as
    they are evaluated, ``and``/``or``, conditional expressions and chained
    comparisons may skip their later operands, loops may run zero times and
    repeat, a handler or ``finally`` may start anywhere in its ``try``, and
    only ``contextlib.suppress`` among context managers is taken to swallow
    an exception, as the definite-assignment analysis takes it.
    """
    names = frozenset(renamed)
    if not names:
        return frozenset()
    declared = {
        name
        for statement in block
        for node in ast.walk(statement)
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }
    reads = _UnboundReads(names, names & _names_unbound_anywhere(block))
    reads.run(block, frozenset())
    return frozenset((names & declared) | reads.found)


_State = Optional[FrozenSet[str]]
"""The renamed binders bound on every path to a point; None when no path reaches it."""


def _meet(left: _State, right: _State) -> _State:
    if left is None:
        return right
    if right is None:
        return left
    return left & right


def _names_unbound_anywhere(statements: Iterable[ast.AST]) -> Set[str]:
    """Names some statement deletes: a ``del`` target, or an ``except ... as`` name at its clause's end."""
    names: Set[str] = set()
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
                names.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
    return names


def _irrefutable(pattern: ast.AST) -> bool:
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or _irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(_irrefutable(alternative) for alternative in pattern.patterns)
    return False


def _suppresses(expression: ast.AST) -> bool:
    callee = expression.func if isinstance(expression, ast.Call) else expression
    name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
    return name == "suppress"


_TRY_STATEMENTS: Tuple[type, ...] = tuple(
    kind for kind in (ast.Try, getattr(ast, "TryStar", None)) if isinstance(kind, type)
)

_Comprehension = Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]


class _UnboundReads:
    """Which of ``names`` the block may read while unbound, walking it in evaluation order.

    ``found`` collects them. The state threaded through is the set of those
    names bound on every path to the current point; code with a scope of its
    own (a function, lambda, class body or comprehension) may run whenever it
    is called, so its reads count as unbound unless the name is bound where
    the scope is created and nothing in the block ever unbinds it.
    """

    def __init__(self, names: FrozenSet[str], unbound_anywhere: AbstractSet[str]) -> None:
        self.names = names
        self.unbound_anywhere = unbound_anywhere
        self.found: Set[str] = set()

    # -- bindings and reads --------------------------------------------------

    def _read(self, name: str, state: FrozenSet[str]) -> None:
        if name in self.names and name not in state:
            self.found.add(name)

    def _bind(self, state: FrozenSet[str], names: Iterable[str]) -> FrozenSet[str]:
        return state | (self.names & frozenset(names))

    def _nested(
        self, parts: Iterable[ast.AST], own: AbstractSet[str], state: FrozenSet[str]
    ) -> None:
        """Reads by code of a scope of its own, which binds ``own``: they may run later, or often."""
        for part in parts:
            for name in _free_reads(part, frozenset(own)) & self.names:
                if name not in state or name in self.unbound_anywhere:
                    self.found.add(name)

    # -- statements ------------------------------------------------------------

    def run(self, statements: Sequence[ast.stmt], state: _State) -> _State:
        for statement in statements:
            if state is None:
                return None
            state = self._statement(statement, state)
        return state

    def _statement(self, node: ast.stmt, state: FrozenSet[str]) -> _State:
        if isinstance(node, ast.Expr):
            return self._expression(node.value, state)
        if isinstance(node, ast.Assign):
            state = self._expression(node.value, state)
            for target in node.targets:
                state = self._target(target, state)
            return state
        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                self._read(node.target.id, state)
                return self._bind(self._expression(node.value, state), [node.target.id])
            return self._expression(node.value, self._target_parts(node.target, state))
        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                return (
                    state
                    if isinstance(node.target, ast.Name)
                    else self._target_parts(node.target, state)
                )
            return self._target(node.target, self._expression(node.value, state))
        if isinstance(node, ast.Delete):
            for target in node.targets:
                state = self._delete(target, state)
            return state
        if isinstance(node, (ast.Return, ast.Raise)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    state = self._expression(child, state)
            return None
        if isinstance(node, (ast.Break, ast.Continue)):
            return None
        if isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal)):
            return state
        if isinstance(node, ast.Assert):
            holds, fails = self._condition(node.test, state)
            if node.msg is not None:
                self._expression(node.msg, fails)
            return holds
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return self._bind(state, import_binding_names(node))
        if isinstance(node, ast.If):
            holds, fails = self._condition(node.test, state)
            return _meet(self.run(node.body, holds), self.run(node.orelse, fails))
        if isinstance(node, (ast.For, ast.AsyncFor)):
            # The body may run many times, after any unbinding in the loop.
            entry = self._expression(node.iter, state) - _names_unbound_anywhere([node])
            self.run(node.body, self._target(node.target, entry))
            self.run(node.orelse, entry)
            return entry
        if isinstance(node, ast.While):
            entry = state - _names_unbound_anywhere([node])
            holds, fails = self._condition(node.test, entry)
            self.run(node.body, holds)
            self.run(node.orelse, fails)
            return holds & fails
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                state = self._expression(item.context_expr, state)
                if item.optional_vars is not None:
                    state = self._target(item.optional_vars, state)
            after = self.run(node.body, state)
            if any(_suppresses(item.context_expr) for item in node.items):
                return state - _names_unbound_anywhere(node.body)
            return after
        if isinstance(node, _TRY_STATEMENTS):
            return self._try(node, state)
        if isinstance(node, ast.Match):
            return self._match(node, state)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for expression in [*evaluated_before_definition(node), *annotation_expressions(node)]:
                state = self._expression(expression, state)
            own = set(parameter_names(node.args)).union(
                *(bindings_of(statement, into_nested_scopes=False) for statement in node.body)
            )
            self._nested(node.body, own, state)
            return self._bind(state, [node.name])
        if isinstance(node, ast.ClassDef):
            for expression in [
                *node.decorator_list,
                *node.bases,
                *(keyword.value for keyword in node.keywords),
            ]:
                state = self._expression(expression, state)
            self._nested(node.body, frozenset(), state)
            return self._bind(state, [node.name])
        # Anything else (a ``type`` alias): every read in it may find its name
        # unbound, and nothing it binds is counted on.
        self._nested([node], frozenset(), frozenset())
        return state - bindings_of(node, into_nested_scopes=True)

    def _try(self, node: ast.stmt, state: FrozenSet[str]) -> _State:
        body: List[ast.stmt] = getattr(node, "body")
        handlers: List[ast.ExceptHandler] = getattr(node, "handlers")
        orelse: List[ast.stmt] = getattr(node, "orelse")
        finalbody: List[ast.stmt] = getattr(node, "finalbody")
        after_body = self.run(body, state)
        # A handler may start anywhere in the body.
        anywhere = state - _names_unbound_anywhere(body)
        outcomes: List[_State] = [None if after_body is None else self.run(orelse, after_body)]
        for handler in handlers:
            entry = anywhere
            if handler.type is not None:
                entry = self._expression(handler.type, entry)
            if handler.name:
                entry = self._bind(entry, [handler.name])
            handled = self.run(handler.body, entry)
            if handled is not None and handler.name:
                handled = handled - {handler.name}
            outcomes.append(handled)
        normal = reduce(_meet, outcomes, None)
        if not finalbody:
            return normal
        finished = self.run(finalbody, state - _names_unbound_anywhere([node]))
        if normal is None or finished is None:
            return None
        return (normal - _names_unbound_anywhere(finalbody)) | finished

    def _match(self, node: ast.Match, state: FrozenSet[str]) -> _State:
        state = self._expression(node.subject, state)
        outcomes: List[_State] = []
        exhaustive = False
        for case in node.cases:
            for expression in pattern_expressions(case.pattern):
                self._expression(expression, state)
            matched = self._bind(state, pattern_capture_names(case.pattern))
            if case.guard is not None:
                matched = self._condition(case.guard, matched)[0]
            outcomes.append(self.run(case.body, matched))
            exhaustive = exhaustive or (case.guard is None and _irrefutable(case.pattern))
        if not exhaustive:
            outcomes.append(state)
        return reduce(_meet, outcomes, None)

    def _target(self, target: ast.AST, state: FrozenSet[str]) -> FrozenSet[str]:
        if isinstance(target, ast.Name):
            return self._bind(state, [target.id])
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                state = self._target(element, state)
            return state
        if isinstance(target, ast.Starred):
            return self._target(target.value, state)
        return self._target_parts(target, state)

    def _target_parts(self, target: ast.AST, state: FrozenSet[str]) -> FrozenSet[str]:
        """What a subscript or attribute target evaluates before it is stored to or deleted."""
        if isinstance(target, ast.Subscript):
            return self._expression(target.slice, self._expression(target.value, state))
        if isinstance(target, ast.Attribute):
            return self._expression(target.value, state)
        if isinstance(target, ast.expr):
            return self._expression(target, state)
        return state

    def _delete(self, target: ast.AST, state: FrozenSet[str]) -> FrozenSet[str]:
        if isinstance(target, ast.Name):
            self._read(target.id, state)
            return state - {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                state = self._delete(element, state)
            return state
        return self._target_parts(target, state)

    # -- expressions -----------------------------------------------------------

    def _condition(
        self, node: ast.expr, state: FrozenSet[str]
    ) -> Tuple[FrozenSet[str], FrozenSet[str]]:
        """The state where ``node`` was true, and where it was false."""
        if isinstance(node, ast.BoolOp):
            conjunction = isinstance(node.op, ast.And)
            current = state
            exits: List[FrozenSet[str]] = []
            for value in node.values:
                holds, fails = self._condition(value, current)
                # ``and`` goes on while its operands hold, ``or`` while they fail.
                current, leaving = (holds, fails) if conjunction else (fails, holds)
                exits.append(leaving)
            left_early = reduce(frozenset.intersection, exits)
            return (current, left_early) if conjunction else (left_early, current)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            holds, fails = self._condition(node.operand, state)
            return fails, holds
        after = self._expression(node, state)
        return after, after

    def _expression(self, node: ast.expr, state: FrozenSet[str]) -> FrozenSet[str]:
        """Check the reads of ``node`` and return the state after it is evaluated."""
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self._read(node.id, state)
            return state
        if isinstance(node, ast.NamedExpr):
            return self._bind(self._expression(node.value, state), [node.target.id])
        if isinstance(node, ast.BoolOp):
            holds, fails = self._condition(node, state)
            return holds & fails
        if isinstance(node, ast.IfExp):
            holds, fails = self._condition(node.test, state)
            return self._expression(node.body, holds) & self._expression(node.orelse, fails)
        if isinstance(node, ast.Compare):
            state = self._expression(node.left, state)
            state = self._expression(node.comparators[0], state)
            later = state
            # A chained comparison stops at the first that fails.
            for comparator in node.comparators[1:]:
                later = self._expression(comparator, later)
            return state
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is not None:
                    state = self._expression(key, state)
                state = self._expression(value, state)
            return state
        if isinstance(node, ast.Lambda):
            for default in evaluated_before_definition(node):
                state = self._expression(default, state)
            self._nested([node.body], frozenset(parameter_names(node.args)), state)
            return state
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return self._comprehension(node, state)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                state = self._expression(child, state)
            elif isinstance(child, ast.keyword):
                state = self._expression(child.value, state)
        return state

    def _comprehension(self, node: _Comprehension, state: FrozenSet[str]) -> FrozenSet[str]:
        """The first iterable is evaluated here; the rest in the comprehension's own scope."""
        state = self._expression(node.generators[0].iter, state)
        targets = {
            name.id
            for generator in node.generators
            for name in ast.walk(generator.target)
            if isinstance(name, ast.Name)
        }
        inner: List[ast.AST] = [
            *(generator.iter for generator in node.generators[1:]),
            *(condition for generator in node.generators for condition in generator.ifs),
        ]
        collected = _Collected()
        visit_comprehension_result(collected, node)
        self._nested([*inner, *collected.nodes], targets, state)
        return state


def _free_reads(node: ast.AST, own: FrozenSet[str]) -> Set[str]:
    """The names ``node`` reads that neither ``own`` nor a scope inside ``node`` binds.

    A comprehension's targets, a lambda's parameters and a function's
    parameters and locals are theirs; a class body binds nothing its reads
    are counted against.
    """
    if isinstance(node, ast.Name):
        return (
            {node.id} if isinstance(node.ctx, (ast.Load, ast.Del)) and node.id not in own else set()
        )
    found: Set[str] = set()
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        if node.target.id not in own:
            found.add(node.target.id)
    if isinstance(node, ast.Lambda):
        for default in evaluated_before_definition(node):
            found |= _free_reads(default, own)
        return found | _free_reads(node.body, own | frozenset(parameter_names(node.args)))
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        found |= _free_reads(node.generators[0].iter, own)
        inside = own | frozenset(
            name.id
            for generator in node.generators
            for name in ast.walk(generator.target)
            if isinstance(name, ast.Name)
        )
        parts: List[ast.AST] = [
            *(generator.iter for generator in node.generators[1:]),
            *(condition for generator in node.generators for condition in generator.ifs),
        ]
        collected = _Collected()
        visit_comprehension_result(collected, node)
        for part in [*parts, *collected.nodes]:
            found |= _free_reads(part, inside)
        return found
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for expression in [*evaluated_before_definition(node), *annotation_expressions(node)]:
            found |= _free_reads(expression, own)
        inside = own | frozenset(parameter_names(node.args)).union(
            *(bindings_of(statement, into_nested_scopes=False) for statement in node.body)
        )
        for statement in node.body:
            found |= _free_reads(statement, inside)
        return found
    for child in ast.iter_child_nodes(node):
        found |= _free_reads(child, own)
    return found


class _Collected(ast.NodeVisitor):
    """The nodes a visit is handed, unvisited: the parts ``visit_comprehension_result`` names."""

    def __init__(self) -> None:
        self.nodes: List[ast.AST] = []

    def visit(self, node: ast.AST) -> None:
        self.nodes.append(node)


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
