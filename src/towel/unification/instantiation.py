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

The comparison is by binding, not by spelling. Every identifier of both
fragments is rewritten as what it denotes (``_BindingSpeller``): a binder
by its scope and its order there, so each renamed binder matches only its
own occurrences; a free name by where the site reads it, a function scope
around the block or the module and the builtins, where every free name the
helper's own code reads is the module's. The arguments are substituted
scope by scope and marked as the site's, so a lambda, comprehension or
function of the helper that binds a name an argument uses captures it, and
the check reports the capture instead of comparing it equal.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
import copy
from weakref import WeakKeyDictionary
from functools import reduce
from typing import (
    AbstractSet,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
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
from .lexical_scopes import (
    Comprehension,
    NestedScope,
    ScopeNames,
    comprehension_results,
    free_reads,
    innermost_mentioning,
    nested_scope_names,
    statement_list_scope,
)
from .models import FunctionNode
from .parameters import parameter_names, parameter_nodes
from .scope_analyzer import pattern_expressions
from .semantic_safety import walk_own_scope
from .statement_facts import bindings_of, import_binding_names, pattern_capture_names
from .visitors import (
    ScopeVisitor,
    annotation_expressions,
    evaluated_before_definition,
    visit_as,
)
from .structural_memo import structural_id


class InstantiationError(Exception):
    """The helper body cannot be reduced against this call."""


def instantiation_mismatch(
    helper: ast.FunctionDef,
    call_statement: ast.stmt,
    block: Sequence[ast.stmt],
    template_renames: Mapping[str, str],
    block_renames: Mapping[str, str],
    *,
    site_function_names: AbstractSet[str],
    preamble_length: int,
    returns_variables: bool,
) -> Optional[str]:
    """Return why ``helper`` applied to ``call_statement`` differs from ``block``.

    The helper body is built from the template block, so it may spell a bound
    variable by the template's name or by the canonical alpha-renamed name;
    the comparison is by binding, so either spelling of a binder matches the
    block's (``_BindingSpeller``). ``template_renames`` and ``block_renames``
    each map a block's original names to those canonical names; together they
    translate the variables the helper returns to the names the call assigns.
    ``site_function_names`` holds the names the block's site reads from a
    function scope around it, its own locals and parameters or an enclosing
    function's, rather than from the module or the builtins
    (``semantic_safety.function_scope_names``): the helper's own code reads
    every free name from its module, so such a name can only reach it as an
    argument. ``preamble_length`` counts injected global/nonlocal
    declarations at the start of the body and ``returns_variables`` states
    whether the extractor appended a return of the block's live variables.
    """
    site_names = frozenset(site_function_names)
    key = (
        _helper_dump(helper),
        canonical_dump(call_statement),
        structural_id(block),
        tuple(sorted(template_renames.items())),
        tuple(sorted(block_renames.items())),
        tuple(sorted(site_names)),
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
        site_names,
        preamble_length=preamble_length,
        returns_variables=returns_variables,
    )
    _VERDICTS.put(key, _Verdict(verdict))
    return verdict


class _Verdict(NamedTuple):
    """A memoized verdict; the wrapper lets ``None`` (no mismatch) be a cache hit."""

    verdict: Optional[str]


_VERDICTS: BoundedCache[
    Tuple[
        str,
        str,
        str,
        Tuple[Tuple[str, str], ...],
        Tuple[Tuple[str, str], ...],
        Tuple[str, ...],
        int,
        bool,
    ],
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
    site_function_names: FrozenSet[str],
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
    reducer = _Reducer(
        dict(zip(parameters, call.args)),
        arguments_are_site=True,
        body_names=statement_list_scope(body).local - frozenset(parameters),
    )
    try:
        reduced = [visit_as(reducer, statement) for statement in body]
        actual = _binding_form(reduced, site_function_names, fragment_is_site=False)
    except InstantiationError as error:
        return str(error)
    expected = _expected_binding_form(block, site_function_names)
    if actual.dump != expected.dump:
        return f"body: {ast.unparse(actual.module)!r} != {ast.unparse(expected.module)!r}"
    if actual.binders.keys() != expected.binders.keys():
        return "binder correspondence"
    renamed = {own for token, own in expected.binders.items() if actual.binders[token] != own}
    observable = observable_renamings(block, renamed)
    if observable:
        return f"renamed binder observable: {', '.join(sorted(observable))}"
    return None


class _BindingForm(NamedTuple):
    """A fragment with every identifier spelled by its binding, and what its binders were spelled.

    ``binders`` maps each binder's token to the name it had, lambda
    parameters and names imported without ``as`` aside.
    """

    module: ast.Module
    dump: str
    binders: Mapping[str, str]


_EXPECTED_FORMS: "BoundedCache[Tuple[str, Tuple[str, ...]], _BindingForm]" = BoundedCache(16_384)
"""The binding form of a block, by structural id and its site's function-scope names.

A block is checked against every call that reproduces it, once per pair it
forms, and its binding form is a function of its structure and of where its
site reads its free names: the same code re-parsed after a rewrite hits too.
The cached form is never modified. Per process; the workers fork after
parsing and each keeps its own copy.
"""


def _expected_binding_form(
    block: Sequence[ast.stmt], site_function_names: FrozenSet[str]
) -> _BindingForm:
    """``_binding_form`` of a copy of ``block``, memoized on its structure and its site's names."""
    key = (structural_id(block), tuple(sorted(site_function_names)))
    cached = _EXPECTED_FORMS.get(key)
    if cached is None:
        copied = [copy.deepcopy(statement) for statement in block]
        cached = _EXPECTED_FORMS.put(
            key, _binding_form(copied, site_function_names, fragment_is_site=True)
        )
    return cached


def _binding_form(
    statements: List[ast.stmt], site_function_names: FrozenSet[str], *, fragment_is_site: bool
) -> _BindingForm:
    """``statements``, which it rewrites in place, as the body of a function, spelled by binding.

    ``fragment_is_site`` says the statements are the block itself; otherwise
    they are a reduced helper body, whose nodes copied from the call's
    arguments are the site's (``_Reducer``). Raises ``InstantiationError``
    where a name is captured by a scope of the other origin.
    """
    speller = _BindingSpeller(site_function_names, fragment_is_site=fragment_is_site)
    speller.fragment(statements)
    module = ast.Module(body=statements, type_ignores=[])
    return _BindingForm(module, canonical_dump(module), dict(speller.binders))


def _alpha_normalize(module: ast.Module) -> ast.Module:
    """A copy of ``module`` spelled by binding, as the block it would be; every free name global.

    Binders are numbered per scope in visiting order, so two fragments that
    differ only in how they spell the names they bind come out alike, and a
    free name keeps its spelling. Names imported without ``as`` name what
    they import and keep theirs. Annotations in a function body are never
    evaluated and take no part.
    """
    copied = [copy.deepcopy(statement) for statement in module.body]
    return _binding_form(copied, frozenset(), fragment_is_site=True).module


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
            for name in free_reads(part, frozenset(own)) & self.names:
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
        self._nested([*inner, *comprehension_results(node)], targets, state)
        return state


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


_FROM_CALL_ARGUMENT = "_towel_from_call_argument"
"""The attribute that marks a node of a reduced helper body as copied from the call's arguments.

An attribute rather than a set of node ids: ``copy.deepcopy`` carries it to
every copy, so a thunk's argument substituted twice is the site's in both
places, and a discarded node's id being reused cannot mislabel another.
"""


def _from_call_argument(node: ast.AST) -> bool:
    return bool(getattr(node, _FROM_CALL_ARGUMENT, False))


class _Reducer(ast.NodeTransformer):
    """Replace parameter names by arguments, beta-reducing thunk calls, scope by scope.

    A parameter's name inside a lambda, comprehension, function or class of
    the body that binds the same spelling is that scope's own name, not the
    parameter, and stays. The substitution is capture-checked: an argument
    was evaluated where the call stands (a thunk's argument, where the
    helper calls it), so a name it reads that ``body_names`` or a scope
    around the parameter's position binds would denote another variable
    there, and ``InstantiationError`` names it. With ``arguments_are_site``
    the substituted copies are marked as the site's (``_FROM_CALL_ARGUMENT``),
    so the binding form resolves their names where the call stands; a
    thunk's arguments are the helper's own expressions and keep their marks.
    """

    def __init__(
        self,
        arguments: Mapping[str, ast.expr],
        *,
        arguments_are_site: bool,
        body_names: AbstractSet[str],
    ) -> None:
        self._arguments = arguments
        self._arguments_are_site = arguments_are_site
        self._body_names = body_names
        self._scopes: List[ScopeNames] = []

    def _argument_for(self, name: str) -> Optional[ast.expr]:
        if name not in self._arguments or innermost_mentioning(self._scopes, name) is not None:
            return None
        argument = self._arguments[name]
        for read in sorted(free_reads(argument)):
            if read in self._body_names or innermost_mentioning(self._scopes, read) is not None:
                raise InstantiationError(f"argument captured: {read!r} where {name} stands")
        return argument

    def _copied(self, argument: ast.expr) -> ast.expr:
        copied = copy.deepcopy(argument)
        if self._arguments_are_site:
            for node in ast.walk(copied):
                setattr(node, _FROM_CALL_ARGUMENT, True)
        return copied

    def visit_Name(self, node: ast.Name) -> ast.AST:
        argument = self._argument_for(node.id)
        return node if argument is None else self._copied(argument)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        function = node.func
        if isinstance(function, ast.Name):
            argument = self._argument_for(function.id)
            if isinstance(argument, ast.Lambda):
                actual_args = [visit_as(self, item) for item in node.args]
                actual_keywords = [visit_as(self, item) for item in node.keywords]
                thunk = self._copied(argument)
                assert isinstance(thunk, ast.Lambda)
                return _beta_reduce(thunk, actual_args, actual_keywords)
        return self.generic_visit(node)

    @contextmanager
    def _inside(self, node: NestedScope) -> Iterator[None]:
        self._scopes.append(nested_scope_names(node))
        try:
            yield
        finally:
            self._scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        _visit_definition_parts(self, node)
        with self._inside(node):
            node.body = visit_as(self, node.body)
        return node

    def _definition(self, node: Union[FunctionNode, ast.ClassDef]) -> ast.AST:
        _visit_definition_parts(self, node)
        with self._inside(node):
            node.body = [visit_as(self, statement) for statement in node.body]
        return node

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def _comprehension(self, node: Comprehension) -> ast.AST:
        first = node.generators[0]
        first.iter = visit_as(self, first.iter)
        with self._inside(node):
            for index, generator in enumerate(node.generators):
                generator.target = visit_as(self, generator.target)
                if index:
                    generator.iter = visit_as(self, generator.iter)
                generator.ifs = [visit_as(self, condition) for condition in generator.ifs]
            if isinstance(node, ast.DictComp):
                node.key = visit_as(self, node.key)
                node.value = visit_as(self, node.value)
            else:
                node.elt = visit_as(self, node.elt)
        return node

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension


def _visit_definition_parts(
    transformer: ast.NodeTransformer,
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda],
) -> None:
    """Transform, in the enclosing scope, what a definition evaluates where it stands.

    Decorators, defaults, parameter and return annotations, bases and
    keywords; the body is the caller's to transform in the new scope.
    """
    if not isinstance(node, ast.Lambda):
        node.decorator_list = [visit_as(transformer, item) for item in node.decorator_list]
    if isinstance(node, ast.ClassDef):
        node.bases = [visit_as(transformer, item) for item in node.bases]
        node.keywords = [visit_as(transformer, item) for item in node.keywords]
        return
    arguments = node.args
    arguments.defaults = [visit_as(transformer, item) for item in arguments.defaults]
    arguments.kw_defaults = [
        visit_as(transformer, item) if item is not None else None for item in arguments.kw_defaults
    ]
    if isinstance(node, ast.Lambda):
        return
    for parameter in parameter_nodes(arguments):
        if parameter.annotation is not None:
            parameter.annotation = visit_as(transformer, parameter.annotation)
    if node.returns is not None:
        node.returns = visit_as(transformer, node.returns)


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
    original block up to the thunks the extractor introduced. ``thunk`` is the
    reducer's own copy, marked as the site's; the actual arguments are the
    helper's expressions and keep their marks wherever they are substituted.
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
        return ast.Call(func=body.func, args=actual_args, keywords=actual_keywords)
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
    reducer = _Reducer(
        bindings,
        arguments_are_site=False,
        body_names=nested_scope_names(thunk).local - frozenset(bindings),
    )
    return visit_as(reducer, thunk.body)


_BOUND = "bound:"
_IMPORTED = "import:"
_MODULE = "module:"
_FUNCTION = "local:"
"""Token prefixes. None can begin an identifier, so no token spells a name of the program.

A binder is ``bound:<scope>:<n>``, numbered per scope in visiting order; a
name imported without ``as`` is ``import:<name>``, since it names what is
imported; a free name is ``local:<name>`` where the site reads it from a
function scope around the block and ``module:<name>`` where it reads the
module's namespace or the builtins, as every free name of the helper does.
"""


class _OpenScope:
    """A scope of the fragment being spelled: its names, its origin, and its binders' tokens."""

    def __init__(
        self, names: ScopeNames, fixed: FrozenSet[str], site: bool, index: int, is_lambda: bool
    ) -> None:
        self.names = names
        self.fixed = fixed
        self.site = site
        self.index = index
        self.is_lambda = is_lambda
        self.tokens: Dict[str, str] = {}

    def token(self, name: str) -> str:
        if name in self.fixed:
            return _IMPORTED + name
        known = self.tokens.get(name)
        if known is None:
            known = self.tokens[name] = f"{_BOUND}{self.index}:{len(self.tokens)}"
        return known


class _BindingSpeller(ScopeVisitor):
    """Rewrite a fragment's identifiers in place as the bindings they denote.

    The fragment is a function body: the block, or the reduced helper body.
    A name resolves in the innermost scope that binds it (a class body only
    from its own code), following Python's static scoping. In the helper, a
    name copied from a call argument belongs to the site, and a scope the
    helper's own code opens cannot bind it, nor can a scope an argument
    opened bind the helper's names; either is a capture and raises
    ``InstantiationError``. A free name is ``local:`` or ``module:`` by where
    the site reads it (``site_function_names``); a free name of the helper's
    own code is always ``module:``, since the helper reads it from its
    module. So a name the extractor left spelled as the template's, where
    the site reads a local of its function, never compares equal to the
    site's read, whatever renaming relates their spellings.
    """

    def __init__(self, site_function_names: FrozenSet[str], *, fragment_is_site: bool) -> None:
        self._site_function_names = site_function_names
        self._fragment_is_site = fragment_is_site
        self._scopes: List[_OpenScope] = []
        self._opened = 0
        self._in_lambda_parameters = False
        self.binders: Dict[str, str] = {}

    # -- the fragment and its scopes -------------------------------------------

    def fragment(self, statements: Sequence[ast.stmt]) -> None:
        self._open(statement_list_scope(statements), _imported_as_themselves(statements), None)
        for statement in statements:
            self.visit(statement)
        self._scopes.pop()

    def _is_site(self, node: Optional[ast.AST]) -> bool:
        if self._fragment_is_site:
            return True
        return node is not None and _from_call_argument(node)

    def _open(self, names: ScopeNames, fixed: FrozenSet[str], node: Optional[ast.AST]) -> None:
        self._scopes.append(
            _OpenScope(
                names, fixed, self._is_site(node), self._opened, isinstance(node, ast.Lambda)
            )
        )
        self._opened += 1

    def _enter_scope(self, node: ast.AST) -> object:
        scope_node = cast(NestedScope, node)
        fixed = (
            _imported_as_themselves(scope_node.body)
            if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else frozenset()
        )
        self._open(nested_scope_names(scope_node), fixed, node)
        return None

    def _leave_scope(self, node: ast.AST) -> None:
        self._scopes.pop()

    # -- resolution ----------------------------------------------------------------

    def _resolve(self, name: str, node: ast.AST, scopes: Optional[List[_OpenScope]] = None) -> str:
        chain = self._scopes if scopes is None else scopes
        site = self._is_site(node)
        innermost = len(chain) - 1
        for index in range(innermost, -1, -1):
            scope = chain[index]
            if scope.names.is_class and index != innermost:
                continue
            if not scope.names.mentions(name):
                continue
            if scope.site != site:
                whose = "an argument's" if site else "the helper's"
                raise InstantiationError(f"captured: {whose} {name!r}")
            if name in scope.names.local:
                return scope.token(name)
            if name in scope.names.declared_global:
                return _MODULE + name
        if site and name in self._site_function_names:
            return _FUNCTION + name
        return _MODULE + name

    def _bind(self, name: str, node: ast.AST) -> str:
        token = self._resolve(name, node)
        if token.startswith(_BOUND) and not self._in_lambda_parameters:
            self.binders[token] = name
        return token

    # -- identifier positions ----------------------------------------------------------

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            node.id = self._resolve(node.id, node)
        else:
            node.id = self._bind(node.id, node)

    def _bind_parameters(self, args: ast.arguments) -> None:
        # A lambda's parameters are its signature, compared by position.
        self._in_lambda_parameters = self._scopes[-1].is_lambda
        try:
            for parameter in parameter_nodes(args):
                parameter.arg = self._bind(parameter.arg, parameter)
        finally:
            self._in_lambda_parameters = False

    def _bind_definition_name(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        node.name = self._bind(node.name, node)

    def _bind_target(self, target: ast.AST) -> None:
        self.visit(target)

    def _visit_definition_head(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda]
    ) -> None:
        for expression in evaluated_before_definition(node):
            self.visit(expression)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
        elif not isinstance(node, ast.Lambda):
            for annotation in annotation_expressions(node):
                self.visit(annotation)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            node.name = self._bind(node.name, node)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name:
            node.name = self._bind(node.name, node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            node.name = self._bind(node.name, node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        for key in node.keys:
            self.visit(key)
        for pattern in node.patterns:
            self.visit(pattern)
        if node.rest:
            node.rest = self._bind(node.rest, node)

    def visit_alias(self, node: ast.alias) -> None:
        # ``import a as b`` binds b, which may be spelled otherwise; ``from m
        # import x`` binds the name of what it imports.
        if node.asname is not None:
            node.asname = self._bind(node.asname, node)

    def visit_Global(self, node: ast.Global) -> None:
        node.names = [_MODULE + name for name in node.names]

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        outer = self._scopes[:-1]
        node.names = [self._resolve(name, node, outer) for name in node.names]

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # A function body never evaluates the annotation of a variable; a
        # class body stores it, so there it is part of what runs.
        if not self._scopes[-1].names.is_class:
            node.annotation = ast.Constant(value="annotation")
        self.generic_visit(node)


def _imported_as_themselves(statements: Sequence[ast.stmt]) -> FrozenSet[str]:
    """Names the scope's own imports bind without ``as``: they name what is imported."""
    names: Set[str] = set()
    for statement in statements:
        for node in walk_own_scope(statement):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.asname is None and alias.name != "*":
                        names.add(alias.name.split(".")[0])
    return frozenset(names)
