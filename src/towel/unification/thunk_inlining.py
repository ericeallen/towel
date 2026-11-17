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

"""Pass a thunk's expression eagerly when doing so is unobservable.

A thunk parameter is evaluated inside the helper at the original position.
When every caller proves an immutable result, and the helper evaluates that
thunk before any other effect, exactly once, and on every path, evaluating
the expression at the call site instead
changes nothing an observer can see: the call site's own arguments are
names, literals, tuples of those, lambda creations, or earlier such thunks
in the same order. Unknown results remain deferred because an eager parameter
would retain a temporary beyond its original last use. This pass detects the
eligible situation from the original argument expressions and helper body
and turns the thunk back into an ordinary argument, so the helper reads
``__param_0`` rather than ``__param_0()``.

"Before any other effect" means that every step the helper takes before
the thunk can neither run code nor raise, since either would then happen
after the thunk's effects instead of before them. That is decided step by
step, in CPython's evaluation order, by an allowlist (``_effect_free``):
building a tuple, list, set or dict display, a lambda without defaults or
with effect-free ones, an f-string's joining, a constant, a negative number,
binding a name, looking up a method of a string or bytes literal
(``''.join``), and reading a name that is sure to be bound, which is a
parameter of the helper or a name its prefix has already bound. Every other
step is an effect, and so is every form the allowlist does not name. Among
them are steps with no node of their own (``_Operation``): a set or dict
display hashing an element that is not a constant, which runs ``__hash__``
and, on a collision, ``__eq__``; ``*x`` iterating; ``**m`` reading a
mapping's keys; a target list unpacking its value. A global or builtin name
is an effect: reading it raises ``NameError`` when nothing binds it. Nothing
after a ``return`` or ``raise`` is reached, so no thunk there is inlined.

That is the whole of strict mode's rule.

Relaxed mode passes arguments by value and keeps a thunk only where one is
clearly necessary (docs/DECISIONS.md, "Relaxed mode passes arguments by
value"). ``ValuePolicy`` carries what it assumes. The prefix rule is the
same, with three differences: a thunk's result need not be immutable
(``any_result``); a module-level or builtin name every site resolves is read
without failing (``certain_names``); and an ``if`` whose tests and values
can neither fail nor run code, and which only assigns names, does not end
the prefix (``_quiet_branch``). Every site's expression must read only names
its call site is sure to resolve. An expression, with or without a call, is
never moved past a step that can fail: plain values have no side effects,
but they can raise, and the exception the program raises must not change.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum
from typing import (
    AbstractSet,
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from .compiler_literals import immutable_literal_value
from .semantic_safety import is_eagerly_evaluable
from .statement_facts import loaded_names
from .substitution import Substitution


class _Kind(Enum):
    """What the interpreter does with a value it has evaluated, in a step with no node of its own."""

    HASH = "hash"
    """A set or dict display hashes an element or key, and compares it with any it collides with."""
    MERGE = "merge"
    """``**m`` in a dict display or a call reads the mapping's keys and items."""
    READ = "read"
    """An augmented assignment reads its target name before evaluating its value."""


@dataclass(frozen=True)
class _Operation:
    """An evaluation step that belongs to ``operand`` but is not the evaluation of a node.

    ``display`` is the set or dict display a ``HASH`` inserts into.
    """

    kind: _Kind
    operand: ast.AST
    display: Optional[ast.AST] = None


Step = Union[ast.AST, _Operation]
"""One step of evaluation: a node, once its operands are evaluated, or an operation on a value."""


@dataclass(frozen=True)
class ValuePolicy:
    """What a mode lets this pass assume when it turns a thunk back into a value.

    The default is strict mode's: nothing is assumed, and only the rules that
    are exact apply.
    """

    any_result: bool = False
    """Relaxed: no program observes how long an argument's value is held."""
    plain_values: bool = False
    """Relaxed: reading an attribute, subscripting, comparing and arithmetic have no side effects."""
    available: Sequence[AbstractSet[str]] = ()
    """Per block, the names its call site may read by value wherever the helper used them:
    those it is sure to resolve, less module names a callee may rebind between reads."""
    renames: Sequence[Mapping[str, str]] = ()
    """Per block, the hygienic renames of its names to the helper's spelling."""
    certain_names: FrozenSet[str] = frozenset()
    """Relaxed: module-level and builtin names every site is sure to resolve, which the
    helper reads without failing. Across modules only builtin names qualify,
    subject to the shared helper's namespace checks during placement."""


STRICT = ValuePolicy()


def inline_leading_thunks(
    helper: ast.FunctionDef,
    substitution: Substitution,
    param_order: Dict[str, int],
    policy: ValuePolicy = STRICT,
) -> Set[str]:
    """Pass by value every thunk whose expression may be evaluated at the call instead.

    In strict mode those are the thunks the helper evaluates first, once
    and unconditionally, whose every caller proves an immutable result (an
    eager parameter holds its value until the helper returns, where the
    thunk's result was a temporary). Relaxed mode adds the module
    docstring's rules.

    A call's arguments are evaluated left to right, so a thunk is inlined
    only while it follows, in the signature, every thunk inlined before it:
    each may fail or run code, and must do so in the block's order.
    Returns the inlined parameter names; ``helper`` and ``substitution``
    are updated in place.
    """
    inlined: List[str] = []
    for name in _leading_candidates(helper, substitution, policy):
        if any(param_order[name] <= param_order[earlier] for earlier in inlined):
            break
        inlined.append(name)
    inlined.extend(
        name for name in _movable_anywhere(helper, substitution, policy) if name not in inlined
    )
    return _apply(helper, substitution, inlined)


def _eligible_leading_thunks(
    helper: ast.FunctionDef, substitution: Substitution, policy: ValuePolicy = STRICT
) -> Tuple[str, ...]:
    """The complete certified evaluation prefix, independent of signature discovery order.

    This discovery does not change the helper or substitution.
    """
    return _leading_candidates(helper, substitution, policy)


def _leading_candidates(
    helper: ast.FunctionDef, substitution: Substitution, policy: ValuePolicy
) -> Tuple[str, ...]:
    """The thunks the helper evaluates exactly once, ahead of anything that could tell.

    A thunk is a function parameter with no bound variables. It must come
    before every step that can run code or raise, and its callers must
    prove an immutable result unless the policy says ``any_result``.
    """
    thunks = {name for name, bound in substitution.function_params.items() if not bound}
    if not thunks:
        return ()
    scan = _PrefixScan(helper, substitution, policy, thunks)
    scan.block(helper.body)
    return tuple(scan.found)


class _PrefixScan:
    """Walks the helper's unconditional prefix in evaluation order, collecting its thunks."""

    def __init__(
        self,
        helper: ast.FunctionDef,
        substitution: Substitution,
        policy: ValuePolicy,
        thunks: Set[str],
    ) -> None:
        self.helper = helper
        self.substitution = substitution
        self.policy = policy
        self.thunks = thunks
        self.translation = _Translation(substitution, policy)
        self.found: List[str] = []
        self.bound: FrozenSet[str] = _parameter_names(helper.args)
        # Names the prefix binds, which a site's expression must not read.
        self.rebound: Set[str] = set()
        self._own_names = _parameter_names(helper.args) | {
            node.id
            for node in ast.walk(helper)
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load)
        }
        # Module-level and builtin names the helper reads as its sites do.
        self._certain = policy.certain_names - self._own_names

    def block(self, statements: Sequence[ast.stmt]) -> bool:
        """Scan ``statements``; whether what follows them is still part of the prefix."""
        for statement in statements:
            if isinstance(statement, (ast.Global, ast.Nonlocal, ast.Pass)):
                continue
            if self.policy.plain_values:
                bound = _quiet_branch(statement, self.bound | self._certain)
                if bound is not None:
                    self.rebound |= bound
                    continue
            steps, complete = _evaluation_prefix(statement)
            for step in steps:
                if not self._step(step):
                    return False
            if not complete:
                return False
        return True

    def _step(self, step: Step) -> bool:
        """Account for one step; False when the prefix ends with it."""
        thunk = _thunk_name(step, self.thunks)
        if thunk is not None:
            return self._thunk(thunk)
        if _effect_free(step, self.bound):
            self.bound = self.bound | _names_bound_by(step)
            self.rebound |= _names_bound_by(step)
            return True
        return (
            isinstance(step, ast.Name)
            and isinstance(step.ctx, ast.Load)
            and step.id in self._certain
        )

    def _thunk(self, name: str) -> bool:
        """Whether thunk ``name``, evaluated here, may be evaluated at the call; it ends the prefix if not."""
        if name in self.found or not _single_use(self.helper, name):
            return False
        if self.policy.plain_values and (
            self._reads_rebound(name) or not self._reads_only_available(name)
        ):
            # Relaxed mode also declines to move a read of a name past the
            # prefix's own binding of it, or a read the call site might not
            # resolve; strict mode's immutable results read no name.
            return False
        if not (self.policy.any_result or _immutable_thunk_results(self.substitution, name)):
            return False
        self.found.append(name)
        return True

    def _reads_only_available(self, name: str) -> bool:
        """Whether every site's expression reads only names its call site is sure to resolve.

        A free variable that may be unbound was made a thunk so that the
        helper reads it where the block did (``thunk_of_possibly_unbound_local``).
        """
        available = self.policy.available
        return all(
            index < len(available) and loaded_names(expr) <= available[index]
            for index, expr in self.substitution.param_expressions.get(name, ())
        )

    def _reads_rebound(self, name: str) -> bool:
        """Whether a site's expression reads a name the prefix has bound before it."""
        return any(
            self.translation.at_site(index, self.rebound) & loaded_names(expr)
            for index, expr in self.substitution.param_expressions.get(name, ())
        )


class _Translation:
    """What the helper's names stand for at each site."""

    def __init__(self, substitution: Substitution, policy: ValuePolicy) -> None:
        self.substitution = substitution
        self._inverse = [
            {canonical: original for original, canonical in renames.items()}
            for renames in policy.renames
        ]

    def at_site(self, index: int, names: AbstractSet[str]) -> Set[str]:
        """The names the site's own code reads where the helper reads ``names``.

        A parameter the unifier made stands for the site's expression; a
        free variable for the site's spelling of it; any other name, a local
        of the helper or a module name, is taken to be spelled alike.
        """
        inverse = self._inverse[index] if index < len(self._inverse) else {}
        translated: Set[str] = set()
        for name in names:
            expressions = self.substitution.param_expressions.get(name)
            if expressions is not None:
                for site, expression in expressions:
                    if site == index:
                        translated |= loaded_names(expression)
                continue
            spelled = inverse.get(name, name)
            spelled = self.substitution.aug_assign_mappings.get(name, {}).get(index, spelled)
            translated.add(spelled)
            promoted = self.substitution.promoted_literal_args.get(name, {}).get(index)
            if promoted is not None:
                translated |= loaded_names(promoted)
        return translated


def _movable_anywhere(
    helper: ast.FunctionDef, substitution: Substitution, policy: ValuePolicy
) -> List[str]:
    """The thunks whose expressions give the same value wherever they are evaluated.

    Every site's expression must be one of these:

    - a value the call site may always pass (``is_eagerly_evaluable``),
      which is a thunk only because another site's expression is not one.
      A thunk whose
      every site passes such a value was deferred for a reason of its own
      (a module name a callee may rebind, an operator on a pooled literal)
      and stays one;
    - in relaxed mode, a display of constants, where the helper evaluates
      the thunk at most once per call: the display makes a new object each
      time it runs, and a call makes one too. Strict mode keeps it deferred:
      what the block adds to the object (``consume([0])`` appending one with
      a finalizer) would live until the helper returns, not until the
      statement that used it ends.

    Anything else that can fail is evaluated where the helper evaluates it,
    unless the prefix scan finds it there exactly once on every path.
    """
    movable: List[str] = []
    for name, bound in substitution.function_params.items():
        expressions = substitution.param_expressions.get(name, ())
        if bound or not expressions:
            continue
        once: Optional[bool] = None
        varies = False
        for index, expression in expressions:
            available = policy.available[index] if index < len(policy.available) else frozenset()
            if not isinstance(expression, ast.expr):
                break
            if is_eagerly_evaluable(expression, available):
                continue
            varies = True
            if policy.any_result and _constant_display(expression):
                if once is None:
                    once = _single_use(helper, name) and _evaluated_at_most_once(helper, name)
                if once:
                    continue
            break
        else:
            if varies:
                movable.append(name)
    return movable


def _constant_display(expression: ast.expr) -> bool:
    """Whether ``expression`` is a list, dict or set display of constants.

    Elements may be such displays or tuples of constants themselves. Nothing
    in one runs code or can fail: a constant key hashes on its builtin type
    (``_hashes_quietly``).
    """
    if isinstance(expression, ast.List):
        return all(_constant_element(element) for element in expression.elts)
    if isinstance(expression, ast.Dict):
        return all(
            key is not None
            and isinstance(key, ast.Constant)
            and _hashes_quietly(key, expression)
            and _constant_element(value)
            for key, value in zip(expression.keys, expression.values)
        )
    if isinstance(expression, ast.Set):
        return all(
            isinstance(element, ast.Constant) and _hashes_quietly(element, expression)
            for element in expression.elts
        )
    return False


def _constant_element(expression: ast.expr) -> bool:
    if isinstance(expression, ast.Tuple):
        return all(_constant_element(element) for element in expression.elts)
    if isinstance(expression, ast.UnaryOp):
        return is_eagerly_evaluable(expression, frozenset())
    return isinstance(expression, ast.Constant) or _constant_display(expression)


def _quiet_branch(statement: ast.stmt, bound: AbstractSet[str]) -> Optional[FrozenSet[str]]:
    """The names an ``if`` that only assigns names may bind; None for any other statement.

    Every path through such a statement can neither fail nor run code, and
    falls through to the next statement: its values are effect-free given
    ``bound``, the names sure to be bound before it, its tests are identity
    comparisons of such values (whose truth is a ``bool``), and it holds
    nothing but assignments to names, ``pass`` and more of the same.
    """
    if not isinstance(statement, ast.If):
        return None
    assigned: Set[str] = set()
    pending: List[ast.stmt] = [statement]
    while pending:
        current = pending.pop()
        if isinstance(current, ast.If):
            if not _identity_test(current.test, bound):
                return None
            pending.extend([*current.body, *current.orelse])
        elif isinstance(current, ast.Assign):
            if not effect_free(current.value, bound):
                return None
            for target in current.targets:
                if not isinstance(target, ast.Name):
                    return None
                assigned.add(target.id)
        elif isinstance(current, ast.AnnAssign):
            if not isinstance(current.target, ast.Name):
                return None
            if current.value is not None:
                if not effect_free(current.value, bound):
                    return None
                assigned.add(current.target.id)
        elif not isinstance(current, ast.Pass):
            return None
    return frozenset(assigned)


def _identity_test(test: ast.expr, bound: AbstractSet[str]) -> bool:
    """Whether ``test`` is ``a is b`` or ``a is not b`` over effect-free operands, or ``not`` of one."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _identity_test(test.operand, bound)
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.Is, ast.IsNot))
        and effect_free(test.left, bound)
        and effect_free(test.comparators[0], bound)
    )


def _evaluated_at_most_once(helper: ast.FunctionDef, name: str) -> bool:
    """Whether no call of the helper evaluates ``name()`` twice.

    False when its one occurrence is in the body or test of a loop, in a
    comprehension past its first iterable, or in the body of a lambda or a
    nested function, which may run any number of times.
    """

    def repeated(node: ast.AST, inside: bool) -> Optional[bool]:
        if isinstance(node, ast.Name) and node.id == name:
            return inside
        parts: List[Tuple[ast.AST, bool]]
        if isinstance(node, (ast.For, ast.AsyncFor)):
            parts = [(node.iter, inside), (node.target, True)]
            parts += [(child, True) for child in node.body]
            parts += [(child, inside) for child in node.orelse]
        elif isinstance(node, ast.While):
            parts = [(node.test, True)]
            parts += [(child, True) for child in node.body]
            parts += [(child, inside) for child in node.orelse]
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # The first iterable is found first, where it is evaluated once.
            parts = [(node.generators[0].iter, inside)]
            parts += [(child, True) for child in ast.iter_child_nodes(node)]
        elif isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            parts = [(child, True) for child in ast.iter_child_nodes(node)]
        else:
            parts = [(child, inside) for child in ast.iter_child_nodes(node)]
        for child, child_inside in parts:
            found = repeated(child, child_inside)
            if found is not None:
                return found
        return None

    for statement in helper.body:
        found = repeated(statement, False)
        if found is not None:
            return not found
    return False


def _ordered_thunk_slots(arguments: Sequence[ast.arg], ranks: Mapping[str, int]) -> List[ast.arg]:
    """Permute only contiguous certified slots, retaining every argument node unchanged."""
    ordered = list(arguments)
    start = 0
    while start < len(ordered):
        if ordered[start].arg not in ranks:
            start += 1
            continue
        end = start + 1
        while end < len(ordered) and ordered[end].arg in ranks:
            end += 1
        ordered[start:end] = sorted(ordered[start:end], key=lambda argument: ranks[argument.arg])
        start = end
    return ordered


def reorder_eligible_thunk_parameters(
    helper: ast.FunctionDef,
    substitution: Substitution,
    param_order: Dict[str, int],
    policy: ValuePolicy = STRICT,
) -> Dict[str, int]:
    """Order certified thunk slots in an owned helper signature before eager inlining.

    Free variables, eager parameters and other noncandidates keep their slots.
    The positional-only boundary cannot move. Defaults, variadic arguments and
    keyword-only signatures remain untouched. Only the argument lists change;
    annotations, locations, body and substitution retain their original nodes.
    A changed signature gets a complete position map, including free variables.
    """
    arguments = helper.args
    if (
        arguments.defaults
        or arguments.vararg is not None
        or arguments.kwarg is not None
        or arguments.kwonlyargs
        or arguments.kw_defaults
    ):
        return param_order
    parameters = [*arguments.posonlyargs, *arguments.args]
    if len({argument.arg for argument in parameters}) != len(parameters):
        return param_order
    ranks = {
        name: index
        for index, name in enumerate(_eligible_leading_thunks(helper, substitution, policy))
    }
    positional_only = _ordered_thunk_slots(arguments.posonlyargs, ranks)
    ordinary = _ordered_thunk_slots(arguments.args, ranks)
    if positional_only == arguments.posonlyargs and ordinary == arguments.args:
        return param_order
    arguments.posonlyargs = positional_only
    arguments.args = ordinary
    return {argument.arg: index for index, argument in enumerate((*positional_only, *ordinary))}


def _immutable_thunk_results(substitution: Substitution, name: str) -> bool:
    """Every caller proves a value whose extra parameter reference cannot affect release.

    Evaluation order alone does not certify lifetime: a temporary consumed
    inside the helper would otherwise remain owned by its eager parameter.
    Names, calls, attributes and subscripts have unknown results, regardless
    of annotations or whether their reads are definitely bound.
    """
    expressions = substitution.param_expressions.get(name, ())
    return bool(expressions) and all(
        immutable_literal_value(expression)
        or (
            isinstance(expression, ast.UnaryOp)
            and isinstance(expression.op, (ast.UAdd, ast.USub, ast.Invert))
            and isinstance(expression.operand, ast.Constant)
            and type(expression.operand.value) in (bool, int, float, complex)
        )
        for _, expression in expressions
    )


def effect_free(expression: ast.expr, bound: AbstractSet[str] = frozenset()) -> bool:
    """Whether evaluating ``expression`` can neither run code nor raise.

    ``bound`` holds the names sure to be bound where it is evaluated. An
    expression that evaluates part of itself conditionally or repeatedly (a
    boolean operator, a conditional expression, a chained comparison, a
    comprehension) is not judged effect-free.
    """
    names = frozenset(bound)
    try:
        for step in _walk(expression):
            if not _effect_free(step, names):
                return False
            names = names | _names_bound_by(step)
    except _Stop:
        return False
    return True


def reads_first(expression: ast.expr, names: Sequence[str]) -> bool:
    """Whether evaluating ``expression`` reads ``names``, in that order, before anything else.

    Every path through ``expression`` must read each of them, and before any
    other step: reading another name, an attribute or a subscript, or
    calling. A function of ``names`` called as ``callback(*names)`` where the
    expression stood then reads them exactly when the expression did, and
    fails as it did when one is unbound. ``value if flag else 0`` and ``flag
    and value`` read ``value`` only on some paths; ``a.x + b.y`` reads ``b``
    after ``a.x``, which may fail first. An expression that binds one of
    ``names`` (``(item := …)``) would bind the function's parameter instead.
    """
    if any(
        isinstance(node, ast.NamedExpr)
        and isinstance(node.target, ast.Name)
        and node.target.id in names
        for node in ast.walk(expression)
    ):
        return False
    pending = list(names)
    try:
        for step in _walk(expression):
            if not pending:
                return True
            if not (
                isinstance(step, ast.Name)
                and isinstance(step.ctx, ast.Load)
                and step.id in names
                and (step.id == pending[0] or step.id not in pending)
            ):
                return False
            if step.id == pending[0]:
                pending.pop(0)
    except _Stop:
        return False
    return not pending


def _parameter_names(arguments: ast.arguments) -> FrozenSet[str]:
    """The names a call binds on entry to a function with ``arguments``."""
    listed = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    starred = [arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None]
    return frozenset(arg.arg for arg in listed + starred)


def _apply(helper: ast.FunctionDef, substitution: Substitution, names: Sequence[str]) -> Set[str]:
    for name in names:
        del substitution.function_params[name]
        substitution.params_used_as_callee.discard(name)
        substitution.inlined_parameters.add(name)
    if names:
        _ThunkCallRemover(set(names)).visit(helper)
    return set(names)


def _thunk_name(step: Step, candidates: Set[str]) -> Optional[str]:
    if (
        isinstance(step, ast.Call)
        and isinstance(step.func, ast.Name)
        and step.func.id in candidates
        and not step.args
        and not step.keywords
    ):
        return step.func.id
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


# -- Which steps are effect-free ------------------------------------------------

_LITERAL_METHODS: Mapping[type, FrozenSet[str]] = {
    str: frozenset(
        "capitalize center count encode endswith expandtabs find format index isalnum"
        " isalpha isdecimal isdigit isidentifier islower isnumeric isprintable isspace"
        " istitle isupper join ljust lower lstrip partition replace rfind rindex rjust"
        " rpartition rsplit rstrip split splitlines startswith strip swapcase title"
        " translate upper zfill".split()
    ),
    bytes: frozenset(
        "capitalize center count decode endswith expandtabs find index isalnum isalpha"
        " isdigit islower isspace istitle isupper join ljust lower lstrip partition"
        " replace rfind rindex rjust rpartition rsplit rstrip split splitlines"
        " startswith strip swapcase title translate upper zfill".split()
    ),
}
"""Methods of a string or bytes literal that every Python 3 defines.

Looking one up runs no code, since the literal's type is a builtin one that
cannot be patched, and cannot raise, since the method is there on the
oldest interpreter a project may run. A method newer than that
(``removeprefix``) could raise ``AttributeError`` on it, and is not listed.
"""

_NUMBERS = (int, float, complex)


def _effect_free(step: Step, bound: AbstractSet[str]) -> bool:
    """Whether this step, its operands already evaluated, can neither run code nor raise.

    An allowlist of Python's evaluation steps; anything it does not name is
    an effect.
    """
    if isinstance(step, _Operation):
        if step.kind is _Kind.READ:
            return isinstance(step.operand, ast.Name) and step.operand.id in bound
        return step.kind is _Kind.HASH and _hashes_quietly(step.operand, step.display)
    if isinstance(step, ast.Name):
        # Loading may raise NameError or UnboundLocalError; deleting may too.
        # Binding runs nothing.
        return (
            step.id in bound if isinstance(step.ctx, ast.Load) else isinstance(step.ctx, ast.Store)
        )
    if isinstance(step, (ast.Tuple, ast.List)):
        # Built from evaluated elements; a target list unpacks, which iterates.
        return isinstance(step.ctx, ast.Load)
    if isinstance(
        step, (ast.Constant, ast.Set, ast.Dict, ast.JoinedStr, ast.Lambda, ast.NamedExpr)
    ):
        # Their elements' hashing, their pieces' formatting, and their
        # defaults are steps of their own; what remains allocates or binds.
        return True
    if isinstance(step, ast.Attribute):
        return (
            isinstance(step.ctx, ast.Load)
            and isinstance(step.value, ast.Constant)
            and step.attr in _LITERAL_METHODS.get(type(step.value.value), frozenset())
        )
    if isinstance(step, ast.UnaryOp):
        # A negative number is written as one: ``-1``.
        return (
            isinstance(step.op, (ast.USub, ast.UAdd))
            and isinstance(step.operand, ast.Constant)
            and type(step.operand.value) in _NUMBERS
        )
    return False


def _hashes_quietly(element: ast.AST, display: Optional[ast.AST]) -> bool:
    """Whether a display inserts ``element`` without running code, raising, or warning.

    A constant hashes on its builtin type. Inserting it compares it with
    any key whose hash it shares, which warns under ``python -b`` (and
    raises under ``-bb``) when bytes meets a string or a number, so a
    display whose constants mix bytes with anything else is not quiet.
    """
    if not isinstance(element, ast.Constant):
        return False
    keys: Sequence[Optional[ast.expr]] = (
        display.elts
        if isinstance(display, ast.Set)
        else display.keys if isinstance(display, ast.Dict) else ()
    )
    kinds = {isinstance(key.value, bytes) for key in keys if isinstance(key, ast.Constant)}
    return len(kinds) <= 1


def _names_bound_by(step: Step) -> FrozenSet[str]:
    """The names a step binds, which are sure to be bound for every step after it."""
    if isinstance(step, ast.Name) and isinstance(step.ctx, ast.Store):
        return frozenset({step.id})
    if isinstance(step, ast.NamedExpr) and isinstance(step.target, ast.Name):
        return frozenset({step.target.id})
    return frozenset()


class _ThunkCallRemover(ast.NodeTransformer):
    def __init__(self, names: Set[str]) -> None:
        self._names = names

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if isinstance(node.func, ast.Name) and node.func.id in self._names and not node.args:
            return ast.copy_location(ast.Name(id=node.func.id, ctx=ast.Load()), node)
        return self.generic_visit(node)


# -- The order a statement evaluates in ---------------------------------------


def _evaluation_prefix(statement: ast.stmt) -> Tuple[List[Step], bool]:
    """Steps a statement takes unconditionally and exactly once, in order.

    The flag is True when the whole statement is such a prefix, so the next
    statement is also reached unconditionally. Compound statements yield
    only the part evaluated before any branch, loop, or exception context,
    and nothing after a ``return`` or ``raise`` is reached at all: a thunk
    there is never evaluated, and the raise is itself an effect.
    """
    steps: List[Step] = []
    try:
        if isinstance(statement, ast.Expr):
            steps.extend(_expression_order(statement.value))
            return steps, True
        if isinstance(statement, ast.Return):
            if statement.value is not None:
                steps.extend(_expression_order(statement.value))
            return steps, False
        if isinstance(statement, ast.Assign):
            steps.extend(_expression_order(statement.value))
            for target in statement.targets:
                steps.extend(_target_order(target))
            return steps, True
        if isinstance(statement, ast.AnnAssign):
            # A function scope never evaluates the annotation.
            if statement.value is not None:
                steps.extend(_expression_order(statement.value))
                steps.extend(_target_order(statement.target))
            elif not isinstance(statement.target, ast.Name):
                steps.extend(_target_order(statement.target))
            return steps, True
        if isinstance(statement, ast.AugAssign):
            if isinstance(statement.target, ast.Name):
                steps.append(_Operation(_Kind.READ, statement.target))
            else:
                steps.extend(_target_order(statement.target))
            steps.extend(_expression_order(statement.value))
            steps.append(statement)  # the in-place operation itself
            return steps, True
        if isinstance(statement, ast.Delete):
            for target in statement.targets:
                steps.extend(_target_order(target))
            return steps, True
        if isinstance(statement, ast.Raise):
            if statement.exc is not None:
                steps.extend(_expression_order(statement.exc))
            if statement.cause is not None:
                steps.extend(_expression_order(statement.cause))
            return steps, False
        if isinstance(statement, ast.If):
            steps.extend(_expression_order(statement.test))
        elif isinstance(statement, (ast.For, ast.AsyncFor)):
            steps.extend(_expression_order(statement.iter))
        elif isinstance(statement, (ast.With, ast.AsyncWith)) and statement.items:
            steps.extend(_expression_order(statement.items[0].context_expr))
        elif isinstance(statement, ast.Match):
            steps.extend(_expression_order(statement.subject))
        # While re-evaluates its test; Try routes exceptions; Assert depends on -O.
        return steps, False
    except _Stop as stop:
        steps.extend(stop.steps)
        return steps, False


class _Stop(Exception):
    """Evaluation reached a conditional, repeated, or deferred region."""

    def __init__(self, steps: List[Step]) -> None:
        super().__init__()
        self.steps = steps


def _expression_order(expression: ast.AST) -> List[Step]:
    collected: List[Step] = []
    try:
        for step in _walk(expression):
            collected.append(step)
    except _Stop:
        raise _Stop(collected)
    return collected


def _target_order(target: ast.AST) -> List[Step]:
    """The steps of assigning to, or deleting, ``target``, after the value is evaluated."""
    if isinstance(target, ast.Name):
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        # Unpacking the value comes first, then each element's store.
        steps: List[Step] = [target]
        for element in target.elts:
            steps.extend(_target_order(element))
        return steps
    if isinstance(target, ast.Starred):
        return _target_order(target.value)
    if isinstance(target, ast.Attribute):
        return _expression_order(target.value) + [target]
    if isinstance(target, ast.Subscript):
        return _expression_order(target.value) + _expression_order(target.slice) + [target]
    return [target]


def _walk(node: ast.AST) -> Iterator[Step]:
    """Yield steps in CPython evaluation order until a region that may not run once."""
    for kinds, walker in _WALKERS:
        if isinstance(node, kinds):
            yield from walker(node)
            return
    # Await, Yield, and anything unmodeled: treat as an effect that ends the prefix.
    yield node


def _walk_leaf(node: ast.AST) -> Iterator[Step]:
    yield node


def _walk_lambda(node: ast.Lambda) -> Iterator[Step]:
    # Creating the function evaluates its defaults, positional then keyword.
    for default in node.args.defaults:
        yield from _walk(default)
    for keyword_default in node.args.kw_defaults:
        if keyword_default is not None:
            yield from _walk(keyword_default)
    yield node


def _walk_attribute(node: ast.Attribute) -> Iterator[Step]:
    yield from _walk(node.value)
    yield node


def _walk_subscript(node: ast.Subscript) -> Iterator[Step]:
    yield from _walk(node.value)
    yield from _walk(node.slice)
    yield node


def _walk_slice(node: ast.Slice) -> Iterator[Step]:
    for part in (node.lower, node.upper, node.step):
        if part is not None:
            yield from _walk(part)


def _walk_call(node: ast.Call) -> Iterator[Step]:
    yield from _walk(node.func)
    for argument in node.args:
        yield from _walk(argument)
    for keyword in node.keywords:
        yield from _walk(keyword.value)
        if keyword.arg is None:
            yield _Operation(_Kind.MERGE, keyword.value)
    yield node


def _walk_starred(node: ast.Starred) -> Iterator[Step]:
    # The iteration, which may happen as soon as the value is evaluated.
    yield from _walk(node.value)
    yield node


def _walk_binop(node: ast.BinOp) -> Iterator[Step]:
    yield from _walk(node.left)
    yield from _walk(node.right)
    yield node


def _walk_unaryop(node: ast.UnaryOp) -> Iterator[Step]:
    yield from _walk(node.operand)
    yield node


def _walk_boolop(node: ast.BoolOp) -> Iterator[Step]:
    yield from _walk(node.values[0])
    raise _Stop([])


def _walk_ifexp(node: ast.IfExp) -> Iterator[Step]:
    yield from _walk(node.test)
    raise _Stop([])


def _walk_compare(node: ast.Compare) -> Iterator[Step]:
    yield from _walk(node.left)
    yield from _walk(node.comparators[0])
    yield node
    if len(node.comparators) > 1:
        raise _Stop([])


def _walk_sequence(node: Union[ast.Tuple, ast.List]) -> Iterator[Step]:
    for element in node.elts:
        yield from _walk(element)
    yield node


def _walk_set(node: ast.Set) -> Iterator[Step]:
    # A large display, or one with ``*x``, inserts each element as it is
    # evaluated, so an element may be hashed before the next is evaluated.
    for element in node.elts:
        yield from _walk(element)
        if not isinstance(element, ast.Starred):
            yield _Operation(_Kind.HASH, element, node)
    yield node


def _walk_dict(node: ast.Dict) -> Iterator[Step]:
    # As for a set: a pair may be inserted as soon as its value is evaluated.
    for key, value in zip(node.keys, node.values):
        if key is None:
            yield from _walk(value)
            yield _Operation(_Kind.MERGE, value)
        else:
            yield from _walk(key)
            yield from _walk(value)
            yield _Operation(_Kind.HASH, key, node)
    yield node


def _walk_joined_str(node: ast.JoinedStr) -> Iterator[Step]:
    for value in node.values:
        yield from _walk(value)
    yield node


def _walk_formatted_value(node: ast.FormattedValue) -> Iterator[Step]:
    # Formatting runs ``__format__`` (or ``__str__``, ``__repr__``), an
    # effect, before any format specification after it would matter.
    yield from _walk(node.value)
    yield node


def _walk_comprehension(
    node: Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp],
) -> Iterator[Step]:
    yield from _walk(node.generators[0].iter)
    raise _Stop([])


def _walk_named_expr(node: ast.NamedExpr) -> Iterator[Step]:
    yield from _walk(node.value)
    yield node


#: Node kinds in evaluation-order terms and the generator that walks each; first match wins.
_WALKERS: Tuple[Tuple[Union[type, Tuple[type, ...]], Callable[[Any], Iterator[Step]]], ...] = (
    ((ast.Name, ast.Constant), _walk_leaf),
    (ast.Lambda, _walk_lambda),
    (ast.Attribute, _walk_attribute),
    (ast.Subscript, _walk_subscript),
    (ast.Slice, _walk_slice),
    (ast.Call, _walk_call),
    (ast.Starred, _walk_starred),
    (ast.BinOp, _walk_binop),
    (ast.UnaryOp, _walk_unaryop),
    (ast.BoolOp, _walk_boolop),
    (ast.IfExp, _walk_ifexp),
    (ast.Compare, _walk_compare),
    ((ast.Tuple, ast.List), _walk_sequence),
    (ast.Set, _walk_set),
    (ast.Dict, _walk_dict),
    (ast.JoinedStr, _walk_joined_str),
    (ast.FormattedValue, _walk_formatted_value),
    ((ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp), _walk_comprehension),
    (ast.NamedExpr, _walk_named_expr),
)
