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

"""What each rung of the annotation ladder writes, and which refusals end it.

A typed run materializes a proposal once per candidate signature until the
project's checker accepts one (``HelperAnnotationWiring._annotation_ladder``
orders them). This module holds what the rungs are made of, as functions of
the helper and of the checker's answer, so that each is decided from what the
checker said rather than guessed before it spoke:

* :func:`targeted_any` builds the rung that follows a refused ordinary
  signature. It gives ``Any`` to exactly the positions the refusal's errors
  point at and keeps every other annotation, the return type included, as
  precise as it was. The rung that makes every annotation ``Any`` could only
  be as good, and under mypy's ``warn_return_any`` it almost never is: a
  helper returning ``Any`` is refused wherever its value is returned.
* :func:`self_as_type_variable` spells a ``Self`` the sites revealed as a
  type variable bound to their classes, since ``Self`` means nothing in a
  function outside a class and both checkers refuse it there.
* :func:`without_quoted_none` never writes ``None`` as the string ``"None"``.
* :func:`narrowing_the_call_cannot_carry` and
  :func:`declarations_leave_their_class` recognize refusals that no signature
  of the helper can answer, so the ladder ends at the first of them instead of
  paying a project check per remaining rung to learn the same thing.
"""

from __future__ import annotations

import ast
import copy
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import (
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
    cast,
)

from .exceptions import Untypeable
from .models import FunctionNode, RefactoringProposal
from .semantic_safety import walk_own_scope
from ..type_inference import TypeDiagnostic


@dataclass(frozen=True)
class Verified:
    """A variant the project accepted, with the files it would write."""

    files: Dict[str, str]


@dataclass(frozen=True)
class Rejection:
    """The errors a checked variant introduced, and the files it was rendered into.

    ``helper_module`` is the rendered text of the helper's own file; ``files``
    holds every file the variant rewrote, by path, the helper's included.
    """

    errors: Tuple[TypeDiagnostic, ...]
    helper_path: str
    helper_name: str
    helper_module: str
    files: Mapping[str, str] = field(default_factory=dict)

    def confined_to_helper(self) -> bool:
        """Whether every error lies within the helper's own definition.

        Asked of the all-``Any`` helper, this decides whether the unannotated one
        is worth a project check. To a caller the two are the same function: every
        parameter accepts anything and the result constrains nothing. They differ
        only on the helper's own lines, where a project may forbid explicit
        ``Any`` or leave an unannotated body unchecked. An error anywhere else
        survives the change, so the project would reject that helper too. An
        error the checker did not locate is taken to lie inside, which costs a
        check rather than a refactoring.
        """
        spans = [
            _definition_span(node)
            for node in ast.walk(ast.parse(self.helper_module))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == self.helper_name
        ]
        return all(
            error.line is None
            or _same_file(error.path, self.helper_path)
            and any(first <= error.line <= last for first, last in spans)
            for error in self.errors
        )


def _same_file(left: str, right: str) -> bool:
    return os.path.realpath(left) == os.path.realpath(right)


def _definition_span(node: FunctionNode) -> Tuple[int, int]:
    """The first and last line of a definition, its decorators included."""
    first = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
    return first, node.end_lineno or node.lineno


# -- None ---------------------------------------------------------------------


def without_quoted_none(helper: ast.FunctionDef) -> ast.FunctionDef:
    """A copy of ``helper`` in which no annotation is the string ``"None"``.

    ``None`` is the one type spelled by a constant rather than a name, and mypy
    2 with ``native_parser = true`` (packaging and nox enable it) rejects the
    string ``"None"`` as an annotation -- "Invalid type comment or annotation"
    -- while it accepts ``"int | None"`` and ``"Callable[[], None]"``. A string
    is written so that an annotation is never evaluated, and evaluating
    ``None`` is always safe, so it is written bare. Towel quotes an annotation
    whole when it writes it as a string, so only a whole string is rewritten:
    a ``Literal["None"]`` inside one is a string type and stays as it is.
    """
    rewritten = copy.deepcopy(helper)

    def bare(annotation: Optional[ast.expr]) -> Optional[ast.expr]:
        if (
            isinstance(annotation, ast.Constant)
            and isinstance(annotation.value, str)
            and annotation.value.strip() == "None"
        ):
            return ast.copy_location(ast.Constant(value=None), annotation)
        return annotation

    arguments = rewritten.args
    for parameter in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        parameter.annotation = bare(parameter.annotation)
    for variadic in (arguments.vararg, arguments.kwarg):
        if variadic is not None:
            variadic.annotation = bare(variadic.annotation)
    rewritten.returns = bare(rewritten.returns)
    for statement in rewritten.body:
        for node in walk_own_scope(statement):
            if isinstance(node, ast.AnnAssign):
                annotation = bare(node.annotation)
                if annotation is not None:
                    node.annotation = annotation
    return rewritten


# -- Where a site stands ---------------------------------------------------------


@dataclass(frozen=True)
class SiteMethod:
    """The method a call site stands in: its class, how it binds a receiver, and the receiver's name."""

    class_name: str
    kind: str
    """``instance``, ``classmethod`` or ``staticmethod``."""
    receiver: Optional[str]


def method_at(source: str, line: int) -> Optional[SiteMethod]:
    """The method of a module-level class whose body holds ``line``, or None.

    The innermost function holding the line must be a method: a function
    nested in one is not, and neither is anything outside a class.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for statement in tree.body:
        if not isinstance(statement, ast.ClassDef):
            continue
        if not statement.lineno <= line <= (statement.end_lineno or statement.lineno):
            continue
        for member in statement.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not member.lineno <= line <= (member.end_lineno or member.lineno):
                continue
            nested = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                and node is not member
                and node.lineno <= line <= (getattr(node, "end_lineno", None) or node.lineno)
                for node in ast.walk(member)
            )
            if nested:
                return None
            decorators = {
                decorator.id
                for decorator in member.decorator_list
                if isinstance(decorator, ast.Name)
            }
            kind = (
                "staticmethod"
                if "staticmethod" in decorators
                else (
                    "classmethod"
                    if "classmethod" in decorators
                    or member.name in {"__new__", "__init_subclass__"}
                    else "instance"
                )
            )
            positional = [*member.args.posonlyargs, *member.args.args]
            receiver = positional[0].arg if positional and kind != "staticmethod" else None
            return SiteMethod(statement.name, kind, receiver)
    return None


# -- Self ---------------------------------------------------------------------


def _unquoted(annotation: ast.expr) -> Tuple[ast.expr, bool]:
    """The expression a string annotation spells, and whether it was a string."""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return ast.parse(annotation.value.strip(), mode="eval").body, True
        except SyntaxError:
            return annotation, False
    return annotation, False


def _requoted(expression: ast.expr, quoted: bool, like: ast.expr) -> ast.expr:
    written = ast.Constant(value=ast.unparse(expression)) if quoted else expression
    return ast.copy_location(written, like)


def _is_self(node: ast.AST) -> bool:
    """``Self``, or ``typing.Self`` however the module is spelled."""
    if isinstance(node, ast.Name):
        return node.id == "Self"
    return (
        isinstance(node, ast.Attribute) and node.attr == "Self" and isinstance(node.value, ast.Name)
    )


def _mentions_self(annotation: Optional[ast.expr]) -> bool:
    if annotation is None:
        return False
    expression, _ = _unquoted(annotation)
    return any(_is_self(node) for node in ast.walk(expression))


class _SelfToVariable(ast.NodeTransformer):
    def __init__(self, variable: str) -> None:
        self.variable = variable

    def visit_Name(self, node: ast.Name) -> ast.expr:
        return (
            ast.copy_location(ast.Name(id=self.variable, ctx=ast.Load()), node)
            if _is_self(node)
            else node
        )

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        if _is_self(node):
            return ast.copy_location(ast.Name(id=self.variable, ctx=ast.Load()), node)
        return cast(ast.expr, self.generic_visit(node))


def _self_replaced(annotation: ast.expr, variable: str) -> ast.expr:
    expression, quoted = _unquoted(annotation)
    replaced = _SelfToVariable(variable).visit(copy.deepcopy(expression))
    return _requoted(replaced, quoted, annotation)


def fresh_name(stem: str, reserved: Set[str]) -> str:
    """``stem``, or ``stem`` with a number, whichever is not in ``reserved``."""
    if stem not in reserved:
        return stem
    number = 1
    while f"{stem}{number}" in reserved:
        number += 1
    return f"{stem}{number}"


def _is_any(annotation: Optional[ast.expr]) -> bool:
    if annotation is None:
        return True
    expression, _ = _unquoted(annotation)
    return (isinstance(expression, ast.Name) and expression.id == "Any") or (
        isinstance(expression, ast.Attribute) and expression.attr == "Any"
    )


def self_as_type_variable(
    helper: ast.FunctionDef,
    classes: Sequence[str],
    reserved: Set[str],
    declared_returns: Sequence[Optional[ast.expr]],
    returns_call: bool,
) -> Optional[Tuple[ast.FunctionDef, Tuple[ast.stmt, ...]]]:
    """``helper`` with ``Self`` spelled as a type variable bound to ``classes``, and its declaration.

    Inside a method a site's value can have type ``Self``: ``cls.__new__(cls)``
    in a classmethod returning ``Self``, or ``cls`` itself as ``type[Self]``.
    Revealed at the site and copied into a helper outside any class, ``Self``
    names nothing, and both checkers refuse it ("Self type is only allowed in
    annotations within class definition"). What it stood for is "the class of
    the object the caller has", which a type variable says for a function:
    ``_TowelSelf = TypeVar("_TowelSelf", bound="A | B")``, bound to the classes
    the sites are methods of, so ``cls: type[_TowelSelf] -> _TowelSelf`` gives
    each caller back its own ``Self``. The bound is a string, which a checker
    reads and the interpreter never evaluates.

    The return is the sites' declared return with ``Self`` spelled the same way
    when every site returns the helper's value from a method declaring one that
    mentions ``Self``, and the inferred return could not be written; inference
    asks the checker to relate ``Self`` to ``Self`` outside a class, which it
    cannot. None when no parameter mentions ``Self``: a type variable only the
    return mentions binds to nothing at a call.
    """
    parameters = helper.args.posonlyargs + helper.args.args
    if not any(_mentions_self(parameter.annotation) for parameter in parameters) or not classes:
        return None
    variable = fresh_name("_TowelSelf", reserved)
    alias = fresh_name("_towel_typevar", reserved | {variable})
    rewritten = copy.deepcopy(helper)
    for parameter in rewritten.args.posonlyargs + rewritten.args.args:
        if parameter.annotation is not None and _mentions_self(parameter.annotation):
            parameter.annotation = _self_replaced(parameter.annotation, variable)
    if rewritten.returns is not None and _mentions_self(rewritten.returns):
        rewritten.returns = _self_replaced(rewritten.returns, variable)
    elif returns_call and _is_any(rewritten.returns):
        spellings = {ast.dump(_unquoted(declared)[0]) for declared in declared_returns if declared}
        first = declared_returns[0] if declared_returns else None
        if (
            first is not None
            and len(spellings) == 1
            and all(declared is not None for declared in declared_returns)
            and _mentions_self(first)
        ):
            rewritten.returns = _self_replaced(first, variable)
    bound = " | ".join(dict.fromkeys(classes))
    declarations: Tuple[ast.stmt, ...] = (
        ast.ImportFrom(module="typing", names=[ast.alias(name="TypeVar", asname=alias)], level=0),
        ast.Assign(
            targets=[ast.Name(id=variable, ctx=ast.Store())],
            value=ast.Call(
                func=ast.Name(id=alias, ctx=ast.Load()),
                args=[ast.Constant(value=variable)],
                keywords=[ast.keyword(arg="bound", value=ast.Constant(value=bound))],
            ),
        ),
    )
    return (
        ast.fix_missing_locations(rewritten),
        tuple(ast.fix_missing_locations(declaration) for declaration in declarations),
    )


# -- Reading a refusal ----------------------------------------------------------


@dataclass(frozen=True)
class _Call:
    """One call of the helper as rendered: where it stands, and what it passes for each parameter."""

    path: str
    function: Optional[FunctionNode]
    statement: ast.stmt
    span: Tuple[int, int]
    """The lines the call's own statement occupies: its header, for a compound statement."""
    arguments: Mapping[str, ast.expr]
    positional: Tuple[str, ...]
    """The parameter each positional argument fills, in order: what "Argument N" counts."""
    bound: FrozenSet[str]
    """Names the call's statement binds: what the caller receives from the helper."""


class _RenderedProject:
    """The files a refused variant was rendered into, parsed once, and its helper's calls there."""

    def __init__(self, rejection: Rejection) -> None:
        self.rejection = rejection
        self._trees: Dict[str, Optional[ast.Module]] = {}
        texts = dict(rejection.files)
        texts.setdefault(rejection.helper_path, rejection.helper_module)
        self.texts = {os.path.realpath(path): text for path, text in texts.items()}
        self.definition = self._definition()

    def tree(self, path: str) -> Optional[ast.Module]:
        key = os.path.realpath(path)
        if key not in self._trees:
            text = self.texts.get(key)
            try:
                self._trees[key] = ast.parse(text) if text is not None else None
            except SyntaxError:
                self._trees[key] = None
        return self._trees[key]

    def _definition(self) -> Optional[FunctionNode]:
        tree = self.tree(self.rejection.helper_path)
        if tree is None:
            return None
        found = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == self.rejection.helper_name
        ]
        return found[0] if len(found) == 1 else None

    def in_helper(self, error: TypeDiagnostic) -> bool:
        if self.definition is None or error.line is None:
            return False
        first, last = _definition_span(self.definition)
        return _same_file(error.path, self.rejection.helper_path) and first <= error.line <= last

    def parameters(self) -> List[str]:
        if self.definition is None:
            return []
        arguments = self.definition.args
        return [argument.arg for argument in (*arguments.posonlyargs, *arguments.args)]

    def dispatches(self) -> bool:
        """Whether the rendered helper is a method its calls reach through a receiver."""
        definition = self.definition
        if definition is None:
            return False
        tree = self.tree(self.rejection.helper_path)
        in_class = tree is not None and any(
            isinstance(node, ast.ClassDef) and definition in node.body for node in ast.walk(tree)
        )
        static = any(
            isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
            for decorator in definition.decorator_list
        )
        return in_class and not static

    def calls(self) -> Iterator[_Call]:
        """Every call of the helper in the rendered files, with its arguments by parameter."""
        name = self.rejection.helper_name
        parameters = self.parameters()
        receiver = parameters[0] if self.dispatches() and parameters else None
        for path in self.texts:
            tree = self.tree(path)
            if tree is None:
                continue
            for function, statement in _statements(tree):
                for node in _own_nodes(statement):
                    if not isinstance(node, ast.Call):
                        continue
                    callee = node.func
                    by_attribute = isinstance(callee, ast.Attribute) and callee.attr == name
                    if not (isinstance(callee, ast.Name) and callee.id == name or by_attribute):
                        continue
                    positional = (
                        parameters[1:] if receiver is not None and by_attribute else parameters
                    )
                    filled = tuple(positional[: len(node.args)])
                    arguments: Dict[str, ast.expr] = dict(zip(filled, node.args))
                    if receiver is not None and by_attribute and isinstance(callee, ast.Attribute):
                        arguments[receiver] = callee.value
                    for keyword in node.keywords:
                        if keyword.arg is not None:
                            arguments[keyword.arg] = keyword.value
                    simple = not any(
                        isinstance(getattr(statement, name, None), list)
                        for name in ("body", "handlers", "cases")
                    )
                    first = statement.lineno if simple else node.lineno
                    last = (statement.end_lineno if simple else node.end_lineno) or first
                    yield _Call(
                        path,
                        function,
                        statement,
                        (first, last),
                        arguments,
                        filled,
                        _bound_by(statement),
                    )


def _statements(tree: ast.Module) -> Iterator[Tuple[Optional[FunctionNode], ast.stmt]]:
    """Every simple statement of ``tree`` with the innermost function it belongs to."""

    def visit(
        body: Sequence[ast.stmt], function: Optional[FunctionNode]
    ) -> Iterator[Tuple[Optional[FunctionNode], ast.stmt]]:
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from visit(statement.body, statement)
                continue
            if isinstance(statement, ast.ClassDef):
                yield from visit(statement.body, function)
                continue
            yield function, statement
            for name in ("body", "orelse", "finalbody"):
                inner = getattr(statement, name, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    yield from visit(inner, function)
            for clause in (*getattr(statement, "handlers", ()), *getattr(statement, "cases", ())):
                yield from visit(clause.body, function)

    yield from visit(tree.body, None)


def _own_nodes(statement: ast.stmt) -> Iterator[ast.AST]:
    """The nodes of ``statement`` itself, not of the statements nested in it."""
    pending: List[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.stmt):
                pending.append(child)


def _bound_by(statement: ast.stmt) -> FrozenSet[str]:
    targets: List[ast.expr] = []
    if isinstance(statement, ast.Assign):
        targets = list(statement.targets)
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        targets = [statement.target]
    return frozenset(
        node.id
        for target in targets
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    )


def _dotted(node: ast.expr) -> Optional[str]:
    """``a.b.c`` for an attribute chain rooted at a name, else None."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _references_on_line(function: Optional[FunctionNode], tree: ast.Module, line: int) -> Set[str]:
    """Every name and attribute chain read on ``line`` of the function holding it."""
    scope: ast.AST = function if function is not None else tree
    found: Set[str] = set()
    for node in ast.walk(scope):
        if getattr(node, "lineno", None) != line:
            continue
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
            spelled = _dotted(node)
            if spelled is not None:
                found.add(spelled)
    return found


def _reads(references: Set[str], reference: str) -> bool:
    """Whether ``reference`` is among ``references``, alone or as the head of a longer chain."""
    return any(read == reference or read.startswith(reference + ".") for read in references)


# -- The targeted rung ----------------------------------------------------------


class _Loosening(Enum):
    """What the targeted rung does to one annotation."""

    WHOLE = "Any"
    RESULT = "Callable[..., Any]'s result"


_ARGUMENT = re.compile(r'^Argument (?P<index>\d+) to "(?P<callee>[^"]+)" has incompatible type')
_RETURNING_ANY = "Returning Any from function declared to return"
_MISSING_ANNOTATION = ("is missing a type annotation", "is missing a return type annotation")
_UNTYPED_CALL = "Call to untyped function"
_VALUE_REFUSED = ("Incompatible return value type", "Incompatible types in assignment")
"""What a checker says on the call's own line when the helper's value is the wrong type."""


def _loosening_on_line(
    definition: FunctionNode, line: int, parameters: Sequence[str]
) -> Dict[str, _Loosening]:
    """What each parameter read on ``line`` of the helper needs: ``Any``, or ``Any`` as its result.

    A parameter only ever called there is a thunk or callback whose result is
    what went wrong, so its ``Callable`` shape is kept.
    """
    parents: Dict[int, ast.AST] = {}
    for node in ast.walk(definition):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    needs: Dict[str, _Loosening] = {}
    for node in ast.walk(definition):
        if not (isinstance(node, ast.Name) and node.id in parameters and node.lineno == line):
            continue
        parent = parents.get(id(node))
        called = isinstance(parent, ast.Call) and parent.func is node
        wanted = _Loosening.RESULT if called else _Loosening.WHOLE
        if needs.get(node.id) is not _Loosening.WHOLE:
            needs[node.id] = wanted
    return needs


class _Return(Enum):
    """What the targeted rung does to the return annotation, weakest first."""

    KEPT = 0
    WITH_ANY = 1
    """``<declared> | Any``: the helper may return ``Any`` and its callers still see the type."""
    ANY = 2


def targeted_any(
    helper: ast.FunctionDef, rejection: Rejection, receiver: Optional[str]
) -> Optional[ast.FunctionDef]:
    """``helper`` with ``Any`` where ``rejection``'s errors point, and nowhere else; None if nowhere.

    An error in the helper's body points at the parameters read on its line;
    one of them only ever called there is a thunk or callback whose result is
    at fault, and becomes ``Callable[[...], Any]`` rather than ``Any``, which
    keeps its arity checked. "Argument N to <helper>" at a call points at that
    parameter. A missing annotation, or a call to the helper as untyped, points
    at every bare position. Everything the errors do not name is kept.

    The return is loosened least of all. ``Returning Any`` or an incompatible
    return value inside the helper makes it ``<declared> | Any``: mypy accepts
    returning ``Any`` there and callers returning the helper's value under the
    declared type, and a caller that uses the value is still checked against
    the declared type. That is the whole of the ``NotImplemented`` rule: the
    constant is typed ``Any`` in typeshed and mypy accepts returning it only
    from a binary dunder, which it recognizes by name, so the helper of two
    ``__eq__`` methods is refused where the methods were not, and ``bool |
    NotImplementedType`` is refused as well. Only an error at a call about the
    value it produced, or on a later line of the calling function that reads
    what the call bound, makes the return ``Any``, since there the declared
    type is what was refused.

    The receiver of a method helper is never annotated: its class fixes it.
    """
    project = _RenderedProject(rejection)
    definition = project.definition
    if definition is None:
        return None
    own = [
        argument.arg
        for argument in (*helper.args.posonlyargs, *helper.args.args)
        if argument.arg != receiver
    ]
    rendered_parameters = project.parameters()
    loosen: Dict[str, _Loosening] = {}
    returned = _Return.KEPT

    def mark(parameter: str, loosening: _Loosening) -> None:
        if parameter in own and loosen.get(parameter) is not _Loosening.WHOLE:
            loosen[parameter] = loosening

    def widen(to: _Return) -> None:
        nonlocal returned
        returned = max(returned, to, key=lambda kind: kind.value)

    def every_bare_position() -> None:
        for argument in (*helper.args.posonlyargs, *helper.args.args):
            if argument.arg in own and _is_any(argument.annotation):
                mark(argument.arg, _Loosening.WHOLE)
        if _is_any(helper.returns):
            widen(_Return.ANY)

    calls = list(project.calls())
    for error in rejection.errors:
        if error.line is None:
            continue
        message = error.message
        if project.in_helper(error):
            if error.line <= definition.lineno and any(
                phrase in message for phrase in _MISSING_ANNOTATION
            ):
                every_bare_position()
            elif message.startswith((_RETURNING_ANY, "Incompatible return value type")):
                widen(_Return.WITH_ANY)
            else:
                for parameter, loosening in _loosening_on_line(
                    definition, error.line, rendered_parameters
                ).items():
                    mark(parameter, loosening)
            continue
        for call in calls:
            if not _same_file(call.path, error.path):
                continue
            at_call = call.span[0] <= error.line <= call.span[1]
            numbered = _ARGUMENT.match(message)
            if (
                at_call
                and numbered is not None
                and numbered.group("callee") == rejection.helper_name
            ):
                index = int(numbered.group("index")) - 1
                if 0 <= index < len(call.positional):
                    mark(call.positional[index], _Loosening.WHOLE)
                break
            if at_call and message.startswith(_UNTYPED_CALL):
                every_bare_position()
                break
            if at_call and message.startswith(_VALUE_REFUSED):
                widen(_Return.ANY)
                break
            if at_call:
                continue
            later = call.function is not None and call.span[1] < error.line <= (
                call.function.end_lineno or call.function.lineno
            )
            tree = project.tree(call.path)
            if later and call.bound and tree is not None:
                read = _references_on_line(call.function, tree, error.line)
                if any(_reads(read, name) for name in call.bound):
                    widen(_Return.ANY)
                    break
    targeted = copy.deepcopy(helper)
    for written in (*targeted.args.posonlyargs, *targeted.args.args):
        wanted = loosen.get(written.arg)
        if wanted is _Loosening.WHOLE:
            written.annotation = ast.Name(id="Any", ctx=ast.Load())
        elif wanted is _Loosening.RESULT:
            written.annotation = _with_any_result(written.annotation)
    if returned is _Return.ANY or (returned is _Return.WITH_ANY and _is_any(targeted.returns)):
        targeted.returns = ast.Name(id="Any", ctx=ast.Load())
    elif returned is _Return.WITH_ANY and targeted.returns is not None:
        expression, quoted = _unquoted(targeted.returns)
        union = ast.BinOp(left=expression, op=ast.BitOr(), right=ast.Name(id="Any", ctx=ast.Load()))
        targeted.returns = _requoted(union, quoted, targeted.returns)
    if ast.dump(targeted) == ast.dump(helper):
        return None
    return ast.fix_missing_locations(targeted)


def _with_any_result(annotation: Optional[ast.expr]) -> ast.expr:
    """``Callable[P, Any]`` for ``Callable[P, R]``; ``Any`` for anything else."""
    anything = ast.Name(id="Any", ctx=ast.Load())
    if annotation is None:
        return anything
    expression, quoted = _unquoted(annotation)
    if (
        isinstance(expression, ast.Subscript)
        and (_dotted(expression.value) or "").rsplit(".", 1)[-1] == "Callable"
        and isinstance(expression.slice, ast.Tuple)
        and len(expression.slice.elts) == 2
    ):
        loosened = copy.deepcopy(expression)
        assert isinstance(loosened.slice, ast.Tuple)
        loosened.slice.elts[1] = anything
        return _requoted(loosened, quoted, annotation)
    return anything


def drop_unbound_variables(
    helper: ast.FunctionDef, declarations: Tuple[ast.stmt, ...]
) -> Tuple[ast.FunctionDef, Tuple[ast.stmt, ...]]:
    """``helper`` and ``declarations`` without a declared type variable no parameter mentions.

    The targeted rung may give ``Any`` to the one parameter that carried a
    variable; left in the return alone, it binds to nothing at a call, which
    both checkers refuse, so the return says ``Any`` instead.
    """
    declared = {
        target.id
        for statement in declarations
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }
    if not declared:
        return helper, declarations
    mentioned: Set[str] = set()
    for argument in (*helper.args.posonlyargs, *helper.args.args):
        if argument.annotation is not None:
            expression, _ = _unquoted(argument.annotation)
            mentioned |= {node.id for node in ast.walk(expression) if isinstance(node, ast.Name)}
    unbound = declared - mentioned
    if not unbound:
        return helper, declarations
    pruned = copy.deepcopy(helper)
    if pruned.returns is not None:
        expression, _ = _unquoted(pruned.returns)
        if any(isinstance(node, ast.Name) and node.id in unbound for node in ast.walk(expression)):
            pruned.returns = ast.Name(id="Any", ctx=ast.Load())
    kept = tuple(
        statement
        for statement in declarations
        if not (
            isinstance(statement, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in unbound for t in statement.targets)
        )
    )
    if not any(isinstance(statement, ast.Assign) for statement in kept):
        kept = ()
    return ast.fix_missing_locations(pruned), kept


# -- Refusals no signature can answer -------------------------------------------


@dataclass(frozen=True)
class Unanswerable:
    """Why no signature of the helper can type the extraction: which reason, and the evidence."""

    reason: Untypeable
    detail: str


_NARROWING_CALLS = frozenset({"isinstance", "issubclass", "callable", "hasattr"})


def _narrows(comparison: ast.Compare) -> bool:
    """Whether a comparison narrows its left operand: identity, or equality with ``None``.

    ``newline >= 0`` leaves ``newline`` an ``int`` and ``key in mapping`` leaves
    ``key`` what it was; neither is a test a guarded expression can rely on.
    """
    for operator, right in zip(comparison.ops, comparison.comparators):
        if isinstance(operator, (ast.Is, ast.IsNot)):
            continue
        if (
            isinstance(operator, (ast.Eq, ast.NotEq))
            and isinstance(right, ast.Constant)
            and right.value is None
        ):
            continue
        return False
    return True


def _tested(test: ast.expr, *, truthiness: bool = True) -> Set[str]:
    """The names and attribute chains a test narrows for the code it governs.

    A narrowing call's first argument (``isinstance`` and its kind), the left
    of an identity comparison or of ``== None`` (``type(x) is C`` narrows
    ``x``), through ``not`` and ``and``/``or``; and, with ``truthiness``, a
    name or chain tested for truth, which narrows an optional value but
    leaves an ``int`` or a ``str`` as it was.
    """
    found: Set[str] = set()
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            found |= _tested(value, truthiness=truthiness)
        return found
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _tested(test.operand, truthiness=truthiness)
    subject: Optional[ast.expr] = None
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Name):
        if test.func.id in _NARROWING_CALLS and test.args:
            subject = test.args[0]
    elif isinstance(test, ast.Compare) and _narrows(test):
        subject = test.left
        if (
            isinstance(subject, ast.Call)
            and isinstance(subject.func, ast.Name)
            and subject.func.id == "type"
            and len(subject.args) == 1
        ):
            subject = subject.args[0]
    elif truthiness:
        subject = test
    if subject is not None and (spelled := _dotted(subject)) is not None:
        found.add(spelled)
    return found


def narrowed_for_later(helper: FunctionNode) -> Set[str]:
    """Names and attribute chains, rooted at parameters, that a test in the helper's body narrows.

    Its own scope only: ``if``, ``while``, conditional expressions and
    ``assert``. An attribute the body merely assigns is left out even though
    the assignment narrows it too, because a method helper's assignment may be
    the attribute's declaration, typed by the helper's own parameters, and then
    an error reading it later is one a signature can correct. A parameter the
    body rebinds is the helper's own and is left out as well: the caller's
    variable of that name is untouched, and one the caller receives back is
    bound by the call.
    """
    parameters = {argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)}
    narrowed: Set[str] = set()
    for statement in helper.body:
        for node in walk_own_scope(statement):
            if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                narrowed |= _tested(node.test)
    rebound = {
        node.id
        for statement in helper.body
        for node in walk_own_scope(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    return {
        reference for reference in narrowed if reference.split(".", 1)[0] in parameters - rebound
    }


def _spelled_at(reference: str, arguments: Mapping[str, ast.expr]) -> Optional[str]:
    """``reference``, written in the helper's parameters, as the call's arguments spell it."""
    head, _, rest = reference.partition(".")
    argument = arguments.get(head)
    spelled = _dotted(argument) if argument is not None else None
    if spelled is None:
        return None
    return f"{spelled}.{rest}" if rest else spelled


def narrowing_the_call_cannot_carry(
    helper: FunctionNode, rejection: Rejection
) -> Optional[Unanswerable]:
    """Why no signature can answer ``rejection``: code after a call relied on the block's narrowing.

    After ``if self._key_cache is None: self._key_cache = _cmpkey(...)`` the
    block's own code, and the code that followed it, read ``self._key_cache``
    as a ``CmpKey``. Moved into a helper, the narrowing ends where the helper
    does, and the caller's ``self._key_cache < other._key_cache`` is back to
    ``CmpKey | None``. A signature says what a function accepts and returns;
    none says what it leaves narrowed in its caller (``TypeIs`` narrows only
    its argument, and only in a condition), so every other rung would be
    refused on the same line.

    Whether the code after the call needed the narrower type is a question of
    declared types, which only the checker can answer; this reads its answer:
    an error on a later line of a calling function that reads what the block
    narrowed, as the call spells it, and nothing the call binds, which the
    helper's return type could still correct. The narrowed references come
    from the helper's own body (:func:`narrowed_for_later`).
    """
    narrowed = narrowed_for_later(helper)
    if not narrowed:
        return None
    project = _RenderedProject(rejection)
    calls = [call for call in project.calls() if call.function is not None]
    for error in rejection.errors:
        if error.line is None or project.in_helper(error):
            continue
        for call in calls:
            function = call.function
            assert function is not None
            if not _same_file(call.path, error.path) or not (
                call.span[1] < error.line <= (function.end_lineno or function.lineno)
            ):
                continue
            tree = project.tree(call.path)
            if tree is None:
                continue
            read = _references_on_line(function, tree, error.line)
            if any(_reads(read, name) for name in call.bound):
                continue
            for reference in sorted(narrowed):
                spelled = _spelled_at(reference, call.arguments)
                if spelled is not None and _reads(read, spelled):
                    return Unanswerable(
                        Untypeable.NARROWING_READ_AFTER_CALL,
                        f"the block narrows {spelled}, and a call cannot carry a narrowing back"
                        f" to its caller ({os.path.basename(error.path)}:{error.line}:"
                        f" {error.message})",
                    )
    return None


_NO_ATTRIBUTE = (
    re.compile(r'^"(?P<cls>[\w.]+)" has no attribute "(?P<attr>\w+)"'),
    re.compile(r'^Item "(?P<cls>[\w.]+)" of "[^"]*" has no attribute "(?P<attr>\w+)"'),
    re.compile(r'^Cannot access attribute "(?P<attr>\w+)" for class "(?P<cls>[\w.]+)'),
)


def assigned_attributes(helper: FunctionNode, parameter: str) -> Set[str]:
    """The attributes the helper's own scope assigns on ``parameter``: ``parameter.a = ...``."""
    assigned: Set[str] = set()
    for statement in helper.body:
        for node in walk_own_scope(statement):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
                and node.value.id == parameter
            ):
                assigned.add(node.attr)
    return assigned


def declarations_leave_their_class(
    helper: FunctionNode, rejection: Rejection, receivers: Mapping[str, FrozenSet[str]]
) -> Optional[Unanswerable]:
    """Why no signature can answer ``rejection``: the block declared its class's attributes.

    A checker learns an instance attribute from the class's body and from the
    assignments its methods make through their own receiver. ``self.style =
    style`` in ``BarColumn.__init__`` is such a declaration; moved into a
    function outside the class it is an assignment to some parameter, and
    ``BarColumn`` no longer has ``style`` wherever it is read. No signature of
    the helper can declare an attribute of another class.

    ``receivers`` maps each helper parameter that some site binds to its own
    method's receiver to the classes those methods belong to. Whether another
    declaration survives -- in the class body, another method, a base class,
    perhaps in a stub outside the project -- only the checker knows, so this
    reads its answer: an error outside the helper saying such a class has no
    attribute the helper assigns through that parameter.
    """
    assigned = {parameter: assigned_attributes(helper, parameter) for parameter in receivers}
    project = _RenderedProject(rejection)
    for error in rejection.errors:
        if project.in_helper(error):
            continue
        for pattern in _NO_ATTRIBUTE:
            match = pattern.match(error.message)
            if match is None:
                continue
            owner = match.group("cls").rsplit(".", 1)[-1]
            attribute = match.group("attr")
            for parameter, classes in receivers.items():
                if owner in classes and attribute in assigned[parameter]:
                    return Unanswerable(
                        Untypeable.ATTRIBUTE_DECLARATIONS,
                        f"the block's assignment {parameter}.{attribute} declared"
                        f" {owner}.{attribute}, and a helper outside {owner} cannot declare its"
                        f" attributes ({os.path.basename(error.path)}:{error.line}:"
                        f" {error.message})",
                    )
    return None


def _governed_thunks(helper: FunctionNode) -> Dict[str, Set[str]]:
    """For each parameter the helper tests, the parameters it calls only where that test governs.

    ``a() if p else b()``, ``if p: ... a() ... else: ... b()``, ``while p:
    a()`` and ``p and a()``: each call of a parameter inside a branch whose
    condition is parameter ``p`` itself, perhaps negated or among the operands
    of ``and``/``or``, is governed by ``p``: the test was passed in as ``p``. A
    condition that merely reads a parameter, ``self.name == other.name``, is
    the helper's own test and :mod:`narrowing` speaks for it. Own scope only.
    """
    parameters = {argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)}
    governed: Dict[str, Set[str]] = {}

    def called_in(nodes: Sequence[ast.AST]) -> Set[str]:
        return {
            node.func.id
            for root in nodes
            for node in walk_own_scope(root)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in parameters
        }

    def tested(test: ast.expr) -> Set[str]:
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return tested(test.operand)
        if isinstance(test, ast.BoolOp):
            return {name for value in test.values for name in tested(value)}
        return {test.id} if isinstance(test, ast.Name) and test.id in parameters else set()

    for statement in helper.body:
        for node in walk_own_scope(statement):
            branches: Sequence[ast.AST] = ()
            condition: Optional[ast.expr] = None
            if isinstance(node, ast.IfExp):
                condition, branches = node.test, (node.body, node.orelse)
            elif isinstance(node, (ast.If, ast.While)):
                condition, branches = node.test, (*node.body, *node.orelse)
            elif isinstance(node, ast.BoolOp):
                condition, branches = node.values[0], tuple(node.values[1:])
            if condition is None:
                continue
            for test in tested(condition):
                governed.setdefault(test, set()).update(called_in(branches) - {test})
    return {test: thunks for test, thunks in governed.items() if thunks}


def _narrowing_test(argument: ast.expr) -> Set[str]:
    """What an argument narrows wherever it is tested, whatever the declared types.

    Only a narrowing call or an identity test: those change a type whenever
    they change anything, since ``x is not None`` is written only where ``x``
    may be ``None``. A value tested for truth narrows an optional one but not
    an ``int`` or a ``str``, which only the checker can tell apart
    (``narrowing_refused_in_thunk``).
    """
    return _tested(argument, truthiness=False)


def _read_in_lambda(thunk: ast.Lambda) -> Set[str]:
    """Names and attribute chains a lambda's body reads from the scope around it."""
    own = {
        argument.arg
        for argument in (*thunk.args.posonlyargs, *thunk.args.args, *thunk.args.kwonlyargs)
    }
    found: Set[str] = set()
    for node in ast.walk(thunk.body):
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(
            getattr(node, "ctx", None), ast.Load
        ):
            spelled = _dotted(node)
            if spelled is not None and spelled.split(".", 1)[0] not in own:
                found.add(spelled)
    return found


def narrowing_needed_in_thunk(
    helper: FunctionNode, calls: Sequence[ast.Call]
) -> Optional[Unanswerable]:
    """Why no signature can type the extraction: a lambda at a call needs the narrowing a test gave it.

    Where two blocks differ in a test and in what it guards, both become
    arguments: ``task.total is not None`` and ``lambda: int(task.total)``
    (rich's progress columns). In the block the test narrowed ``task.total``
    for the expression it guarded; at the call the test is only a ``bool``,
    evaluated beforehand, and the lambda is checked where it is written, in
    the caller, where ``task.total`` is ``float | None`` again. No signature
    of the helper reaches the lambda's body.

    Decided from the proposal alone, as :mod:`narrowing` decides its sibling
    case, because both halves are Towel's own construction: the helper calls
    the thunk only where it tests the test's parameter
    (:func:`_governed_thunks`), and the lambda reads what the test narrows.
    """
    governed = _governed_thunks(helper)
    if not governed:
        return None
    parameters = [argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)]
    for call in calls:
        arguments = dict(zip(parameters, call.args))
        for test_parameter, thunk_parameters in governed.items():
            test = arguments.get(test_parameter)
            if test is None:
                continue
            narrowed = _narrowing_test(test)
            if not narrowed:
                continue
            for thunk_parameter in sorted(thunk_parameters):
                thunk = arguments.get(thunk_parameter)
                if not isinstance(thunk, ast.Lambda):
                    continue
                read = _read_in_lambda(thunk)
                for reference in sorted(narrowed):
                    if _reads(read, reference):
                        return Unanswerable(
                            Untypeable.NARROWING_READ_IN_THUNK,
                            f"{ast.unparse(thunk)} reads {reference}, which"
                            f" {ast.unparse(test)} narrowed where the block evaluated it",
                        )
    return None


def narrowing_refused_in_thunk(
    helper: FunctionNode, rejection: Rejection
) -> Optional[Unanswerable]:
    """Why no signature can answer ``rejection``: a lambda at a call lost a narrowing a truth test gave it.

    The same case as :func:`narrowing_needed_in_thunk` for a test that only
    narrows some types: ``self.total / 2 if self.total else 0`` narrows an
    optional ``total`` and leaves an ``int`` one alone, so only the checker can
    say the lambda needed it. It says so with an error on the lambda's own
    lines, other than one about the call's arguments or its value, which a
    signature could still correct.
    """
    governed = _governed_thunks(helper)
    if not governed:
        return None
    project = _RenderedProject(rejection)
    for call in project.calls():
        for test_parameter, thunk_parameters in governed.items():
            test = call.arguments.get(test_parameter)
            if test is None:
                continue
            narrowed = _tested(test)
            for thunk_parameter in sorted(thunk_parameters):
                thunk = call.arguments.get(thunk_parameter)
                if not isinstance(thunk, ast.Lambda):
                    continue
                read = {r for r in narrowed if _reads(_read_in_lambda(thunk), r)}
                if not read:
                    continue
                first, last = thunk.lineno, thunk.end_lineno or thunk.lineno
                for error in rejection.errors:
                    if (
                        error.line is None
                        or not _same_file(error.path, call.path)
                        or not first <= error.line <= last
                        or _ARGUMENT.match(error.message)
                        or error.message.startswith((_RETURNING_ANY, *_VALUE_REFUSED))
                    ):
                        continue
                    return Unanswerable(
                        Untypeable.NARROWING_READ_IN_THUNK,
                        f"{ast.unparse(thunk)} reads {sorted(read)[0]}, which"
                        f" {ast.unparse(test)} narrowed where the block evaluated it"
                        f" ({os.path.basename(error.path)}:{error.line}: {error.message})",
                    )
    return None


_EMPTY_COLLECTIONS = frozenset({"list", "dict", "set"})
"""Calls that make an empty collection mypy gives a partial type, as ``[]`` and ``{}`` are."""


def _is_empty_collection(value: ast.expr) -> bool:
    if isinstance(value, ast.List) and not value.elts:
        return True
    if isinstance(value, ast.Dict) and not value.keys:
        return True
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _EMPTY_COLLECTIONS
        and not value.args
        and not value.keywords
    )


def _innermost_function(tree: ast.Module, line: int) -> Optional[FunctionNode]:
    found: Optional[FunctionNode] = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.lineno <= line <= (node.end_lineno or node.lineno)
        ):
            if found is None or node.lineno > found.lineno:
                found = node
    return found


def _declares_types(function: FunctionNode) -> bool:
    arguments = function.args
    every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    every += [extra for extra in (arguments.vararg, arguments.kwarg) if extra is not None]
    return function.returns is not None or any(a.annotation is not None for a in every)


def partial_type_passed(
    sites: Sequence[Tuple[str, str, int, ast.Call]], checks_untyped_bodies: Callable[[str], bool]
) -> Optional[Unanswerable]:
    """Why mypy refuses every signature: a call passes a collection whose element type is still open.

    ``attrs = {}`` gives ``attrs`` a partial type, which mypy completes from the
    next statement of the same scope that fills it, ``attrs[key] = value``. In
    the block that statement came next; after the extraction the next thing
    is the call, and a partial type passed to a call is an error at the
    assignment, whatever the parameter is declared as (mistune's
    ``_parse_attrs``: "Need type annotation for attrs"). Pyright has no partial
    types, so this is mypy's alone, and only in a body mypy checks: an
    annotated function, or any where ``check_untyped_defs`` holds.

    ``sites`` are each call's file path, its source, the first line of its
    block, and the call. The collection must be bound once before the block,
    without an annotation, and not read in between.
    """
    for path, source, start_line, call in sites:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        function = _innermost_function(tree, start_line)
        if function is None:
            continue
        if not _declares_types(function) and not checks_untyped_bodies(path):
            continue
        parameters = {
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
                *[a for a in (function.args.vararg, function.args.kwarg) if a is not None],
            )
        }
        own = [node for statement in function.body for node in walk_own_scope(statement)]
        for argument in call.args:
            if not isinstance(argument, ast.Name) or argument.id in parameters:
                continue
            name = argument.id
            bindings = [
                node
                for node in own
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                and node.lineno < start_line
                and any(
                    isinstance(target, ast.Name) and target.id == name
                    for statement_target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in ast.walk(statement_target)
                )
            ]
            other_bindings = [
                node
                for node in own
                if isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and node.lineno < start_line
            ]
            if len(bindings) != 1 or len(other_bindings) != 1:
                continue
            binding = bindings[0]
            if not (
                isinstance(binding, ast.Assign)
                and len(binding.targets) == 1
                and isinstance(binding.targets[0], ast.Name)
                and _is_empty_collection(binding.value)
            ):
                continue
            last = binding.end_lineno or binding.lineno
            read_between = any(
                isinstance(node, ast.Name)
                and node.id == name
                and isinstance(node.ctx, ast.Load)
                and last < node.lineno < start_line
                for node in own
            )
            if read_between:
                continue
            return Unanswerable(
                Untypeable.PARTIAL_TYPE,
                f"{ast.unparse(binding)} ({os.path.basename(path)}:{binding.lineno}) takes its"
                f" element type from the block's first write to {name}, and passed to the"
                " helper first it has none",
            )
    return None


_IMPLIED_NONE = frozenset({"__init__", "__init_subclass__"})
"""Methods mypy gives ``-> None`` when any of their parameters is annotated."""


def unannotated_function(module: ast.Module) -> Optional[str]:
    """The first function of ``module`` that is not fully annotated, or None when every one is.

    Fully annotated is what ``mypy --strict`` asks: every parameter annotated
    but a method's own receiver, and a return annotated but where mypy implies
    ``None`` (an ``__init__`` or ``__init_subclass__`` with an annotated
    parameter). Nested functions count; a lambda cannot be annotated and does
    not.
    """
    methods: Set[int] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef):
            methods.update(
                id(member)
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        arguments = node.args
        positional = [*arguments.posonlyargs, *arguments.args]
        static = any(
            isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
            for decorator in node.decorator_list
        )
        if id(node) in methods and not static and positional:
            positional = positional[1:]
        every = [*positional, *arguments.kwonlyargs]
        every += [extra for extra in (arguments.vararg, arguments.kwarg) if extra is not None]
        if any(argument.annotation is None for argument in every):
            return node.name
        implied = id(node) in methods and node.name in _IMPLIED_NONE and bool(every)
        if node.returns is None and not implied:
            return node.name
    return None


# -- A hearing -----------------------------------------------------------------


Judge = Callable[[ast.FunctionDef, Rejection], Optional[Unanswerable]]
"""Why no signature can answer a refusal of the helper, or None when some signature might."""


class Hearing:
    """What one proposal's ladder has been told so far, and why it stopped, if it stopped early.

    The ladder yields a rung, the caller checks it and reports each refusal
    here, and the ladder reads the hearing before it builds the next rung: the
    targeted rung is made from the ordinary rung's errors. :meth:`settled_by`
    is the first reason the judge gives that no signature can answer a
    refusal, and once there is one the ladder yields nothing more. It is a
    method because the answer changes between the ladder's yields.
    """

    def __init__(self, judge: Judge) -> None:
        self._judge = judge
        self._refusals: List[Tuple[RefactoringProposal, Rejection]] = []
        self._settled_by: Optional[Unanswerable] = None

    def refused(self, variant: RefactoringProposal, rejection: Rejection) -> None:
        """Record that the checker refused ``variant``, and whether that settles the proposal."""
        self._refusals.append((variant, rejection))
        if self._settled_by is None:
            self._settled_by = self._judge(variant.extracted_function, rejection)

    def settle(self, verdict: Unanswerable) -> None:
        """Record that the proposal alone shows no signature can type it; nothing need be checked."""
        if self._settled_by is None:
            self._settled_by = verdict

    def settled_by(self) -> Optional[Unanswerable]:
        """Why no signature can type this proposal, once the proposal or a refusal has shown it."""
        return self._settled_by

    def of(self, variant: RefactoringProposal) -> Optional[Rejection]:
        """The refusal ``variant`` drew, if it was checked and refused."""
        return next((rejection for heard, rejection in self._refusals if heard is variant), None)

    @property
    def last(self) -> Optional[Rejection]:
        """The latest refusal, if any."""
        return self._refusals[-1][1] if self._refusals else None
