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
When the helper evaluates that thunk before any other effect, exactly once,
and on every path, evaluating the expression at the call site instead
changes nothing an observer can see: the call site's own arguments are
names, literals, tuples of those, lambda creations, or earlier such thunks
in the same order. This pass detects that situation from the helper body
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
    bound = _parameter_names(helper.args)
    for statement in helper.body:
        if isinstance(statement, (ast.Global, ast.Nonlocal)):
            continue
        steps, complete = _evaluation_prefix(statement)
        for step in steps:
            thunk = _thunk_name(step, candidates)
            if thunk is not None:
                if thunk in inlined or not _single_use(helper, thunk):
                    return _apply(helper, substitution, inlined)
                if inlined and param_order[thunk] <= param_order[inlined[-1]]:
                    return _apply(helper, substitution, inlined)
                inlined.append(thunk)
                continue
            if not _effect_free(step, bound):
                return _apply(helper, substitution, inlined)
            bound = bound | _names_bound_by(step)
        if not complete:
            break
    return _apply(helper, substitution, inlined)


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
