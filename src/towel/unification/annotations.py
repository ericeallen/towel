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

"""Type annotations for an extracted helper, from its call sites and the project's checker.

Two layers. :func:`annotate_helper` copies: a parameter is annotated when
every call site passes something whose type the site already states (a
parameter of the enclosing function that carries an annotation and is never
rebound, or a literal of a builtin type), and the return when every site
returns the helper's value from a function with a declared return type, when
the helper returns locals the block annotated, or when the helper returns
nothing. :func:`infer_missing_annotations` then asks a ``TypeOracle`` (the
project's mypy or pyright) for the rest: a parameter takes the union of its
sites' revealed types, normalized by the oracle's subtype relation
(:func:`normalize_union`); the return takes the meet of the sites' declared
return types, or the revealed return type when the oracle confirms it is a
subtype of every declaration; thunks take ``Callable`` spellings. Once a
helper carries any annotation, :func:`complete_with_any` fills what is still
bare, so the signature is complete. Without an oracle nothing is inferred
and unions are written unreduced.

An annotation is written unquoted only when it evaluates where the helper is
defined: every name is a builtin, a ``typing`` name the caller imports, a
definition the helper is placed after (:func:`respell_bare`), or, in the
same module, a name bound by a module-level import; and only when evaluating
it is both safe and inert (:func:`_evaluates_at_runtime`, :func:`_is_inert`).
Otherwise it is a string, which never evaluates and which type checkers
resolve in the module. Across modules only builtin names and the caller's
``typing`` names are used, since a site's imports are not the host's.

Inertness is what keeps a refactoring from changing the program. A copied
annotation is a second copy of the site's expression, evaluated once more
than the original evaluated it, at the helper's own definition. An annotation
holding a call -- ``Annotated[int, mark('a')]``, a pydantic ``Field(...)``,
a ``Depends(...)`` -- would therefore run that call one extra time at import,
and an annotation the source had explicitly quoted would run it for the first
time. Neither is visible to a type checker, which reads a string annotation
and the expression it spells as the same type, so the quotation costs nothing
a checker can see and is the whole of the guarantee at run time.

Helpers are annotated only in code that already uses annotations somewhere
among the sites, so an unannotated project stays that way.
"""

from __future__ import annotations

import ast
import builtins
import copy
import textwrap
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, cast

from ..canonical_ast import canonical_dump
from .revealed_types import parse_revealed
from .semantic_safety import walk_own_scope
from ..type_inference import RevealRequest, Subtyping, TypeOracle
from .models import FunctionNode
from .statement_facts import import_binding_names, imported_binding_name
from ..diagnostics import TYPES, debugging
from ..source_text import source_lines

_BUILTIN_NAMES: Set[str] = set(dir(builtins))


@dataclass(frozen=True)
class CallSite:
    """One replacement: the generated call and the function and module it sits in."""

    statement: ast.stmt
    call: ast.Call
    function: FunctionNode
    module: ast.Module
    file_path: str


def call_in_statement(statement: ast.AST, helper_name: str) -> Optional[ast.Call]:
    """The positional call to ``helper_name`` that a generated statement wraps, if any."""
    value: Optional[ast.expr]
    if isinstance(statement, (ast.Return, ast.Expr)):
        value = statement.value
    elif isinstance(statement, ast.Assign):
        value = statement.value
    else:
        return None
    if isinstance(value, ast.Await):
        value = value.value
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == helper_name
        and not value.keywords
        and not any(isinstance(argument, ast.Starred) for argument in value.args)
    ):
        return value
    return None


def annotate_helper(
    helper: ast.FunctionDef,
    sites: Sequence[CallSite],
    host_file: str,
    return_variables: Sequence[str],
) -> ast.FunctionDef:
    """A copy of ``helper`` carrying every annotation the sites agree on."""
    annotated = copy.deepcopy(helper)
    if not sites or not any(_uses_annotations(site.function) for site in sites):
        return annotated
    host = next((site.module for site in sites if site.file_path == host_file), None)
    same_module = all(site.file_path == host_file for site in sites)
    parameters = annotated.args.posonlyargs + annotated.args.args
    for index, parameter in enumerate(parameters):
        candidates = [_argument_annotation(site, index) for site in sites]
        parameter.annotation = _joined(candidates, host, same_module)
    annotated.returns = _met(
        [_return_annotation(site, annotated, return_variables) for site in sites],
        host,
        same_module,
    )
    return annotated


def _uses_annotations(function: FunctionNode) -> bool:
    arguments = function.args
    every = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
    if arguments.vararg:
        every.append(arguments.vararg)
    if arguments.kwarg:
        every.append(arguments.kwarg)
    return function.returns is not None or any(arg.annotation is not None for arg in every)


def _parameter_annotations(function: FunctionNode) -> Dict[str, ast.expr]:
    arguments = function.args
    return {
        arg.arg: arg.annotation
        for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
        if arg.annotation is not None
    }


def _own_scope_nodes(function: FunctionNode) -> Iterator[ast.AST]:
    """Every node of the function's body that is not inside a nested scope."""
    for statement in function.body:
        yield from walk_own_scope(statement)


def _rebinds(function: FunctionNode, name: str) -> bool:
    """Whether the function's own scope binds ``name`` again after its parameter."""
    for node in _own_scope_nodes(function):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        if isinstance(node, ast.ExceptHandler) and node.name == name:
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and name in import_binding_names(node):
            return True
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
            return True
        if isinstance(node, ast.MatchMapping) and node.rest == name:
            return True
    return False


def _argument_annotation(site: CallSite, index: int) -> Optional[ast.expr]:
    """What the site declares about the type of its ``index``-th argument."""
    if index >= len(site.call.args):
        return None
    argument = site.call.args[index]
    if isinstance(argument, ast.Name):
        annotation = _parameter_annotations(site.function).get(argument.id)
        if annotation is None or _rebinds(site.function, argument.id):
            return None
        return annotation
    if isinstance(argument, ast.Constant):
        return _literal_type(argument.value)
    return None


def _literal_type(value: object) -> Optional[ast.expr]:
    # bool first: True is an int.
    for kind in (bool, int, float, complex, str, bytes):
        if type(value) is kind:
            return ast.Name(id=kind.__name__, ctx=ast.Load())
    return None


def _return_annotation(
    site: CallSite, helper: ast.FunctionDef, return_variables: Sequence[str]
) -> Optional[ast.expr]:
    if return_variables:
        declared = _annotated_locals(helper)
        if any(name not in declared for name in return_variables):
            return None
        annotations = [declared[name] for name in return_variables]
        if len(annotations) == 1:
            return annotations[0]
        return ast.Subscript(
            value=ast.Name(id="tuple", ctx=ast.Load()),
            slice=ast.Tuple(elts=[copy.deepcopy(a) for a in annotations], ctx=ast.Load()),
            ctx=ast.Load(),
        )
    if isinstance(site.statement, ast.Return):
        return site.function.returns
    if isinstance(site.statement, ast.Expr):
        return ast.Constant(value=None)
    return None


def _annotated_locals(helper: ast.FunctionDef) -> Dict[str, ast.expr]:
    """Names the helper body binds with an annotated assignment, in its own scope."""
    found: Dict[str, ast.expr] = {}
    for node in _own_scope_nodes(helper):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id not in found
        ):
            found[node.target.id] = node.annotation
    return found


_Subtypes = Callable[[Sequence[Tuple[ast.expr, ast.expr]]], Sequence[Subtyping]]
"""For each ``(narrow, wide)`` pair, the checker's verdict."""


def _unknown_subtypes(pairs: Sequence[Tuple[ast.expr, ast.expr]]) -> Sequence[Subtyping]:
    """The relation without a type checker: only identical spellings are related.

    Towel copies without a checker and reasons only with one; there is no
    second, weaker implementation of subtyping here.
    """
    return [
        Subtyping.YES if canonical_dump(narrow) == canonical_dump(wide) else Subtyping.UNKNOWN
        for narrow, wide in pairs
    ]


_UNRELATED = "_TowelUnrelatedType"
"""A class of the probe's own, which no type the program can name is a subtype of."""

_WITH_UNRELATED = f"\n\nclass {_UNRELATED}:\n    pass\n"

_UNTYPED_NAMES = frozenset({"Any", "Unknown"})
"""How mypy and pyright spell a type they know nothing about, as a whole or within one."""


def _mentions_any(annotation: ast.expr) -> bool:
    """Whether ``annotation`` spells ``Any`` (or pyright's ``Unknown``) anywhere in it."""
    for node in ast.walk(_unquoted(annotation)):
        if isinstance(node, ast.Name) and node.id in _UNTYPED_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _UNTYPED_NAMES:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                quoted = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                continue
            if _mentions_any(quoted):
                return True
    return False


def oracle_subtypes(oracle: TypeOracle, file_path: str, source: str) -> _Subtypes:
    """The relation as the type checker judges it in ``file_path``, where it can judge it.

    A checker asked whether ``narrow`` is assignable to ``wide`` says yes
    whenever either is ``Any``, in whole or in part, or is a class with an
    ``Any`` base, and ``Any`` is what it makes of a name it cannot resolve or
    finds no types for. That yes is no subtype relation: the project's own
    check may see the real type and reject what the normalized union writes.
    So a pair that spells ``Any`` or ``Unknown`` is not asked about, and each
    type is also asked whether it is assignable to a class the probe defines
    for the purpose, which only such a type is; a pair with one of those is
    UNKNOWN. So is every pair of a question the checker answered nothing
    about, as it does where it does not look: ``int`` assignable to that class
    must come back no. A pair spelled the same on both sides is the same type
    and asked as before. Every caller treats UNKNOWN as "not known to be a
    subtype".
    """

    def relation(pairs: Sequence[Tuple[ast.expr, ast.expr]]) -> Sequence[Subtyping]:
        if not pairs:
            return []
        verdicts: List[Optional[Subtyping]] = [None] * len(pairs)
        asked: List[int] = []
        for index, (narrow, wide) in enumerate(pairs):
            same = canonical_dump(_unquoted(narrow)) == canonical_dump(_unquoted(wide))
            if not same and (_mentions_any(narrow) or _mentions_any(wide)):
                verdicts[index] = Subtyping.UNKNOWN
            else:
                asked.append(index)
        if asked:
            spelled = [(ast.unparse(pairs[i][0]), ast.unparse(pairs[i][1])) for i in asked]
            types = list(dict.fromkeys(text for pair in spelled for text in pair))
            questions = [*spelled, *((text, _UNRELATED) for text in types), ("int", _UNRELATED)]
            answers = list(oracle.is_subtype(file_path, source + _WITH_UNRELATED, questions))
            if len(answers) != len(questions) or answers[-1] is not Subtyping.NO:
                answers = [Subtyping.UNKNOWN] * len(questions)  # it did not look
            untyped = {
                text
                for text, answer in zip(types, answers[len(spelled) : -1])
                if answer is not Subtyping.NO
            }
            for index, (narrow_text, wide_text), answer in zip(asked, spelled, answers):
                unrelated = narrow_text in untyped or wide_text in untyped
                verdicts[index] = (
                    Subtyping.UNKNOWN if narrow_text != wide_text and unrelated else answer
                )
        return [verdict or Subtyping.UNKNOWN for verdict in verdicts]

    return relation


def _met(
    candidates: Sequence[Optional[ast.expr]],
    host: Optional[ast.Module],
    same_module: bool,
    subtypes: _Subtypes = _unknown_subtypes,
) -> Optional[ast.expr]:
    """The greatest lower bound of the sites' declared types, when one of them is it.

    Every site returns the helper's value under its own declared return type,
    so the helper's type lies below all of them: their intersection, which
    Python cannot write. When one declared type is a subtype of every other it
    is that intersection. Unrelated declarations leave the return unannotated.
    """
    present = [candidate for candidate in candidates if candidate is not None]
    if not present or len(present) != len(candidates):
        return None
    pairs = [(candidate, other) for candidate in present for other in present]
    verdicts = list(subtypes(pairs))
    width = len(present)
    for index, candidate in enumerate(present):
        if all(verdicts[index * width + j] is Subtyping.YES for j in range(width)):
            return _spelled_for_host(copy.deepcopy(candidate), host, same_module)
    return None


def _union_or_optional_members(expression: ast.expr) -> List[ast.expr]:
    """Members of ``A | B``, ``Union[A, B]`` or ``Optional[A]``; the expression itself otherwise."""
    if isinstance(expression, ast.Subscript) and isinstance(expression.value, ast.Name):
        if expression.value.id == "Optional":
            return _union_or_optional_members(expression.slice) + [ast.Constant(value=None)]
        if expression.value.id == "Union" and isinstance(expression.slice, ast.Tuple):
            return [m for elt in expression.slice.elts for m in _union_or_optional_members(elt)]
    return _union_members(expression)


def _joined(
    candidates: Sequence[Optional[ast.expr]],
    host: Optional[ast.Module],
    same_module: bool,
    extra_bound: Optional[Set[str]] = None,
    subtypes: _Subtypes = _unknown_subtypes,
) -> Optional[ast.expr]:
    """The least upper bound of the sites' types that can be written: their normalized union.

    A helper parameter must accept every site's argument, and a helper may
    return any site's value, so the annotation must be a supertype of each.
    A union is that bound exactly. It is normalized by the subtype relation:
    a member that is a subtype of another member is dropped, so ``int | bool``
    is ``int`` and ``float | int`` is ``float``; duplicates go, ``None`` comes
    last. Any site without a type leaves the annotation off.
    """
    present = [candidate for candidate in candidates if candidate is not None]
    if not present or len(present) != len(candidates):
        return None
    first = present[0]
    if all(canonical_dump(candidate) == canonical_dump(first) for candidate in present[1:]):
        # One spelling everywhere: keep it as written (``Optional[int]`` stays)
        # when the host can write it; otherwise its members may still be
        # writable (``str | None`` needs no import where ``Optional`` does).
        as_written = _spelled_for_host(copy.deepcopy(first), host, same_module, extra_bound)
        if as_written is not None:
            return as_written
    # A quoted member (a forward reference) cannot be joined with ``|`` at
    # runtime: unquote every member and let the whole union be spelled, and
    # quoted as one string if any of its names needs it.
    members = normalize_union(
        [m for candidate in present for m in _union_or_optional_members(_unquoted(candidate))],
        subtypes,
    )
    if not members:
        return None
    union = members[0]
    for member in members[1:]:
        union = ast.BinOp(left=union, op=ast.BitOr(), right=member)
    return _spelled_for_host(union, host, same_module, extra_bound)


def normalize_union(members: Sequence[ast.expr], subtypes: _Subtypes) -> List[ast.expr]:
    """Distinct members with every subtype of another member removed, ``None`` last.

    Between two members that are subtypes of each other (equivalent
    spellings), the first is kept.
    """
    distinct: List[ast.expr] = []
    seen: Set[str] = set()
    for member in members:
        key = canonical_dump(member)
        if key not in seen:
            seen.add(key)
            distinct.append(copy.deepcopy(member))
    if len(distinct) > 1:
        pairs = [(a, b) for a in distinct for b in distinct if a is not b]
        verdicts = dict(zip([(id(a), id(b)) for a, b in pairs], subtypes(pairs)))
        kept: List[ast.expr] = []
        for index, member in enumerate(distinct):
            absorbed = False
            for other_index, other in enumerate(distinct):
                if other is member or verdicts.get((id(member), id(other))) is not Subtyping.YES:
                    continue
                mutual = verdicts.get((id(other), id(member))) is Subtyping.YES
                if not mutual or other_index < index:
                    absorbed = True
                    break
            if not absorbed:
                kept.append(member)
        # A consistent relation cannot absorb every member; verdicts from a
        # checker that could not judge some pairs can (sphinx). Then the
        # union is left as it was rather than emptied.
        distinct = kept or distinct
    none_members = [m for m in distinct if isinstance(m, ast.Constant) and m.value is None]
    others = [m for m in distinct if not (isinstance(m, ast.Constant) and m.value is None)]
    return others + none_members


def _union_members(expression: ast.expr) -> List[ast.expr]:
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.BitOr):
        return _union_members(expression.left) + _union_members(expression.right)
    return [expression]


def _spelled_for_host(
    annotation: ast.expr,
    host: Optional[ast.Module],
    same_module: bool,
    extra_bound: Optional[Set[str]] = None,
) -> Optional[ast.expr]:
    """The annotation as it can be written where the helper is defined, or None.

    ``extra_bound`` names count as bound at the helper's position: names the
    caller will import (``Any``, ``Callable``) and, for a module-level helper,
    definitions the helper will be placed after.
    """
    expression = annotation
    quoted = False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            expression = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
        quoted = True
    resolved = _BUILTIN_NAMES | (extra_bound or set())
    names = _referenced_names(expression)
    if host is not None and _defers_annotations(host):
        return expression
    if names <= resolved:
        if _evaluates_at_runtime(expression, host):
            return expression
        return annotation if quoted else ast.Constant(value=ast.unparse(expression))
    if not same_module or host is None:
        # A whole path means the same in every module, so it is not a site's
        # binding carried into the host: it is written as a string for the
        # caller to import under ``TYPE_CHECKING``, or give up.
        if names - resolved <= _dotted_heads(expression):
            return annotation if quoted else ast.Constant(value=ast.unparse(expression))
        return None
    if names - resolved <= _import_bound_names(host) and _evaluates_at_runtime(expression, host):
        return expression
    return annotation if quoted else ast.Constant(value=ast.unparse(expression))


_RUNTIME_GENERICS = frozenset({"list", "dict", "set", "frozenset", "tuple", "type"})
_TYPING_MODULES = frozenset({"typing", "typing_extensions", "collections.abc"})

_INERT_OPERATORS = (ast.BitOr, ast.USub, ast.UAdd, ast.Invert)
"""The only operators an annotation may apply bare: union, and a negated literal."""

_INERT_NODES = (
    ast.Name,
    ast.Attribute,
    ast.Subscript,
    ast.Slice,
    ast.Tuple,
    ast.List,
    ast.Starred,
    ast.Constant,
    ast.Load,
) + _INERT_OPERATORS
"""Node kinds whose evaluation cannot reach a statement of the project's own."""


def _is_inert(expression: ast.expr) -> bool:
    """Whether evaluating the annotation runs none of the program's own code.

    Copying an annotation copies an expression, and the copy is evaluated
    where the helper is defined: once more than the program evaluated it, or,
    where the source quoted the annotation, once where the program never
    evaluated it at all. ``Annotated[int, mark('a')]`` calls ``mark`` again;
    so do ``Field(...)``, ``Depends(...)`` and every other annotation whose
    metadata is built by a call. The extra call is an observable change in
    behaviour that no type checker reports, because a checker reads the
    annotation for its type and never runs it.

    Only a shape that resolves names and asks the type system to subscript,
    union or spell them is inert: anything else -- a call, a lambda, a
    comprehension, a conditional, an f-string, a walrus -- is written as a
    string instead, which a checker resolves identically and the interpreter
    never evaluates.
    """
    for node in ast.walk(expression):
        if isinstance(node, (ast.BinOp, ast.UnaryOp)):
            if not isinstance(node.op, _INERT_OPERATORS):
                return False
        elif not isinstance(node, _INERT_NODES):
            return False
    return True


def _evaluates_at_runtime(expression: ast.expr, host: Optional[ast.Module]) -> bool:
    """Whether the annotation can be written bare where the helper is defined.

    Two things have to hold, and a failure of either is written as a string.

    Evaluating it must be inert (:func:`_is_inert`): a copied annotation is
    evaluated one more time than the program evaluated the original, so an
    annotation carrying a call would run that call again at import.

    And it must not raise. Every name resolving is not enough:
    ``memoryview[int]`` resolves and raises ``TypeError`` at definition time
    on interpreters where ``memoryview`` is not generic (tornado). A
    subscript is trusted only on a PEP 585 builtin generic, on a name
    imported from ``typing`` or ``collections.abc``, or on ``typing.X`` with
    ``typing`` imported.
    """
    if not _is_inert(expression):
        return False
    generic_names = set(_RUNTIME_GENERICS) | set(_TYPING_NAMES)
    typing_modules: Set[str] = set()
    if host is not None:
        for node in host.body:
            if isinstance(node, ast.ImportFrom) and node.module in _TYPING_MODULES:
                generic_names.update(import_binding_names(node))
            elif isinstance(node, ast.Import):
                typing_modules.update(
                    bound
                    for alias in node.names
                    if alias.name in _TYPING_MODULES
                    and (bound := imported_binding_name(alias)) is not None
                )
    for sub in ast.walk(expression):
        if not isinstance(sub, ast.Subscript):
            continue
        head = sub.value
        if isinstance(head, ast.Name) and head.id in generic_names:
            continue
        if (
            isinstance(head, ast.Attribute)
            and isinstance(head.value, ast.Name)
            and head.value.id in typing_modules
        ):
            continue
        return False
    return True


def _referenced_names(expression: ast.expr) -> Set[str]:
    return {node.id for node in ast.walk(expression) if isinstance(node, ast.Name)}


def _dotted_heads(expression: ast.expr) -> Set[str]:
    """The names that only ever begin a dotted path in ``expression``: ``pkg`` of ``pkg.mod.Name``."""
    bases = {id(node.value) for node in ast.walk(expression) if isinstance(node, ast.Attribute)}
    names = [node for node in ast.walk(expression) if isinstance(node, ast.Name)]
    heads = {name.id for name in names if id(name) in bases}
    return heads - {name.id for name in names if id(name) not in bases}


PythonVersion = Tuple[int, int]
"""A Python release to the minor version: ``(3, 9)``."""

OLDEST_PYTHON: PythonVersion = (3, 0)
"""Assumed where nothing says otherwise: every syntax younger than Python 3 is written as a string."""

_SUBSCRIPTED_CLASSES_SINCE: PythonVersion = (3, 9)
"""PEP 585: ``list[int]``, ``type[C]``, ``collections.abc.Sequence[int]`` at run time."""
_UNION_OPERATOR_SINCE: PythonVersion = (3, 10)
"""PEP 604: ``int | None`` at run time."""
_UNPACKED_SUBSCRIPT_SINCE: PythonVersion = (3, 11)
"""PEP 646: ``tuple[*Ts]``, which older interpreters cannot even parse."""


def _collections_abc_spellings(host: Optional[ast.Module]) -> Tuple[Set[str], Set[str]]:
    """The names ``host`` binds to ``collections.abc`` classes, and to the module itself."""
    names: Set[str] = set()
    modules: Set[str] = {"collections.abc"}
    for node in host.body if host is not None else ():
        if isinstance(node, ast.ImportFrom) and node.module == "collections.abc":
            names.update(import_binding_names(node))
        elif isinstance(node, ast.ImportFrom) and node.module == "collections":
            modules.update(
                alias.asname or alias.name for alias in node.names if alias.name == "abc"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname
                for alias in node.names
                if alias.name == "collections.abc" and alias.asname
            )
    return names, modules


def syntax_needs(expression: ast.expr, host: Optional[ast.Module]) -> PythonVersion:
    """The oldest Python on which evaluating ``expression`` cannot fail for its syntax.

    Towel writes annotations out of three sources -- what the call sites
    declare, how the checker spells what it reveals, and the unions and tuples
    it joins them into -- and a checker spells types the way the newest Python
    does, whatever the project supports. Three forms are younger than the
    rest: a class subscripted, whether a builtin or one of ``collections.abc``
    (3.9); a union written with ``|`` (3.10); and a subscript that unpacks
    (3.11). ``typing``'s own generics, ``Optional`` and ``Union`` work on every
    Python 3 that has ``typing``, and a string is never evaluated at all.
    """
    abc_names, abc_modules = _collections_abc_spellings(host)
    needed = OLDEST_PYTHON
    for node in ast.walk(expression):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            needed = max(needed, _UNION_OPERATOR_SINCE)
        elif isinstance(node, ast.Subscript):
            head = node.value
            if isinstance(head, ast.Name) and (
                head.id in _RUNTIME_GENERICS or head.id in abc_names
            ):
                needed = max(needed, _SUBSCRIPTED_CLASSES_SINCE)
            elif isinstance(head, ast.Attribute) and _dotted_name(head.value) in abc_modules:
                needed = max(needed, _SUBSCRIPTED_CLASSES_SINCE)
            if any(isinstance(item, ast.Starred) for item in ast.walk(node.slice)):
                needed = max(needed, _UNPACKED_SUBSCRIPT_SINCE)
    return needed


def _annotations_run_by(statement: ast.stmt, *, into_classes: bool) -> Iterator[ast.expr]:
    """The annotations executing ``statement`` evaluates, when annotations are not postponed."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = statement.args
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            arguments.vararg,
            arguments.kwarg,
        ):
            if argument is not None and argument.annotation is not None:
                yield argument.annotation
        if statement.returns is not None:
            yield statement.returns
    elif isinstance(statement, ast.AnnAssign):
        yield statement.annotation
    elif isinstance(statement, ast.ClassDef) and into_classes:
        for member in statement.body:
            yield from _annotations_run_by(member, into_classes=False)


def evaluated_syntax(host: ast.Module) -> PythonVersion:
    """The youngest annotation syntax ``host`` already evaluates every time it is imported.

    A module that evaluates ``int | None`` in its own top-level signatures
    raises on any Python before 3.10 as it is imported, so it runs only where
    the same syntax in a helper works too. Only what every import executes is
    evidence: the module's own top-level definitions and annotated names, and
    those of its top-level classes; a definition under an ``if`` or a ``try``
    may never run where the syntax does not work.
    """
    if _defers_annotations(host):
        return OLDEST_PYTHON
    needed = OLDEST_PYTHON
    for statement in host.body:
        for annotation in _annotations_run_by(statement, into_classes=True):
            needed = max(needed, syntax_needs(annotation, host))
    return needed


def written_for_python(
    helper: ast.FunctionDef, host: Optional[ast.Module], oldest: PythonVersion
) -> ast.FunctionDef:
    """A copy of ``helper`` whose annotations each evaluate on ``oldest``, or are strings.

    A module that does not postpone annotations evaluates a function's when
    the function is defined, so ``v: int | None`` in a helper made the whole
    module raise ``TypeError`` on import under Python 3.9, however clean the
    checker, which runs on a newer interpreter, found it. A string is never
    evaluated and a checker reads it as the type it spells: the quotation that
    keeps an annotation's calls from running (``_is_inert``) is the whole cure.
    """
    written = copy.deepcopy(helper)
    if host is not None and _defers_annotations(host):
        return written

    def evaluable(annotation: Optional[ast.expr]) -> Optional[ast.expr]:
        if annotation is None or syntax_needs(annotation, host) <= oldest:
            return annotation
        return ast.copy_location(ast.Constant(value=ast.unparse(annotation)), annotation)

    arguments = written.args
    for parameter in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        parameter.annotation = evaluable(parameter.annotation)
    for variadic in (arguments.vararg, arguments.kwarg):
        if variadic is not None:
            variadic.annotation = evaluable(variadic.annotation)
    written.returns = evaluable(written.returns)
    return written


def _defers_annotations(module: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in module.body
    )


def _import_bound_names(module: ast.Module) -> Set[str]:
    bound: Set[str] = set()
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(import_binding_names(node))
    return bound


def sites_use_annotations(sites: Sequence[CallSite]) -> bool:
    """Whether any site's function declares a type, the style gate for annotating."""
    return any(_uses_annotations(site.function) for site in sites)


_TYPING_NAMES = frozenset({"Any", "Callable", "Literal", "Optional", "Union"})
"""Names an inferred annotation may use that the host must import from ``typing``.

A checker writes ``Union[A, B]`` and ``Optional[A]`` (mypy before 2.0), and a
declared literal as ``Literal[...]``; each is imported where it is written.
"""


def _split_top_level(text: str) -> List[str]:
    """Split on commas outside brackets."""
    parts: List[str] = []
    depth = 0
    current = ""
    for char in text:
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def annotation_from_revealed(
    revealed: str,
    host: Optional[ast.Module],
    same_module: bool,
    bare_ok: Optional[Set[str]] = None,
) -> Optional[ast.expr]:
    """An annotation from a checker's spelling of a type, or None when it cannot be written.

    The spelling is read as :func:`~towel.unification.revealed_types.parse_revealed`
    reads it: a callable is ``Callable[[P], R]``, or ``Callable[..., R]``
    where no parameter list can state it; an inferred literal
    (``Literal['x']?``) is the builtin type its value belongs to, while a
    literal the program declared stays one; a named tuple or typed dict is
    its class. ``builtins.`` is dropped. ``Any`` as the whole type, ``Never``,
    anything the reader cannot read, and an unresolvable name are declined. A
    dotted name is reduced to its last part when that is bound in the host,
    under ``TYPE_CHECKING`` included, and is otherwise kept whole: the checker
    names a class by its whole path, which means the same in every module, and
    the caller imports it under ``TYPE_CHECKING`` where the host can import it,
    or writes ``Any`` where it cannot (:func:`unwritten_as_any`). It used to be
    declined unless its head was bound, and the type-only import that the
    documentation promised was never written.
    """
    expression = parse_revealed(revealed)
    if expression is None:
        return None
    expression = _BuiltinsUnqualified().visit(expression)
    if _dotted_name(expression) in ("Any", "typing.Any") or any(
        isinstance(node, (ast.Name, ast.Attribute)) and _dotted_name(node) in _EMPTY_TYPES
        for node in ast.walk(expression)
    ):
        return None  # Any is what an unannotated parameter already means
    if not _is_type_expression(expression):
        return None
    reduced = _reduce_dotted_names(expression, host)
    return _spelled_for_host(reduced, host, same_module, set(_TYPING_NAMES) | (bare_ok or set()))


_EMPTY_TYPES = frozenset({"Never", "NoReturn", "typing.Never", "typing.NoReturn"})
"""No value has them: a site that reveals one is unreachable, and says nothing about the rest."""


class _BuiltinsUnqualified(ast.NodeTransformer):
    """``builtins.int`` as ``int``, which is how an annotation writes it."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        if isinstance(node.value, ast.Name) and node.value.id == "builtins":
            return ast.copy_location(ast.Name(id=node.attr, ctx=ast.Load()), node)
        return cast(ast.expr, self.generic_visit(node))


def _is_type_expression(expression: ast.expr) -> bool:
    """Whether the tree is made only of what a type annotation is made of.

    Names, attributes, subscripts, tuples, constants, ``|`` unions, and a
    negated number in a literal. A module name that is not an identifier,
    for example, parses as arithmetic.
    """
    for node in ast.walk(expression):
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, ast.BitOr):
                return False
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, ast.USub) or not (
                isinstance(node.operand, ast.Constant) and type(node.operand.value) is int
            ):
                return False
        elif not isinstance(
            node,
            (
                ast.Name,
                ast.Attribute,
                ast.Subscript,
                ast.Tuple,
                ast.List,  # the parameter list of ``Callable[[...], R]``
                ast.Constant,
                ast.Load,
                ast.BitOr,
                ast.USub,
            ),
        ):
            return False
    return True


def _reduce_dotted_names(expression: ast.expr, host: Optional[ast.Module]) -> ast.expr:
    """Rewrite ``pkg.mod.Name`` to the name the host binds for it; keep it whole otherwise."""
    bound = (
        _import_bound_names(host) | _type_only_bound_names(host) | _defined_names(host)
        if host is not None
        else set()
    ) | _TYPING_NAMES
    return cast(ast.expr, _DottedNameReducer(bound).visit(copy.deepcopy(expression)))


def _type_only_bound_names(module: ast.Module) -> Set[str]:
    """What the imports under the module's own ``if TYPE_CHECKING:`` bind, for the checker alone."""
    bound: Set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        guarded = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if guarded:
            for statement in node.body:
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    bound.update(import_binding_names(statement))
    return bound


class _DottedNameReducer(ast.NodeTransformer):
    """Rewrite each ``pkg.mod.Name`` to a name in ``bound`` where one is; leave it whole otherwise."""

    def __init__(self, bound: Set[str]) -> None:
        self.bound = bound

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        head: ast.expr = node
        while isinstance(head, ast.Attribute):
            head = head.value
        if isinstance(head, ast.Name) and head.id in self.bound:
            return node  # ``typing.Sequence`` with ``import typing`` in the host
        if node.attr in self.bound:
            return ast.Name(id=node.attr, ctx=ast.Load())
        return node  # a whole path, for the caller to import or give up


def _defined_names(module: ast.Module) -> Set[str]:
    return {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


@dataclass(frozen=True)
class ApplySite:
    """A replacement as it stands in the file about to be rewritten."""

    file_path: str
    source: str
    start_line: int
    end_line: int
    indent: str
    statement: ast.stmt
    call: ast.Call
    declared_return: Optional[ast.expr] = None


@dataclass(frozen=True)
class _InferredHelper:
    """The helper with inferred annotations, and the imports its host must gain."""

    helper: ast.FunctionDef
    required_imports: Tuple[Tuple[str, str], ...]


def qualified_names_in_annotations(helper: ast.FunctionDef) -> List[str]:
    """Every dotted path the helper's annotations name, longest form first.

    A checker answers with a whole path, ``sphinx.builders.texinfo.TexinfoBuilder``.
    Written into a module that never imports that submodule it is not a name at
    all: the package object carries no such attribute, and the annotation reads
    as undefined even though the head of the path is bound. Which of these the
    host can actually reach is not decidable here, so they are all reported and
    the caller keeps the ones it can give a spelling that works.
    """
    found: List[str] = []

    def collect(node: ast.AST) -> None:
        # A chain's own prefixes are not names the annotation uses, so a chain
        # is taken whole and not descended into.
        if isinstance(node, ast.Attribute):
            dotted = _dotted_name(node)
            if dotted is not None:
                if dotted not in found:
                    found.append(dotted)
                return
        for child in ast.iter_child_nodes(node):
            collect(child)

    for annotation in _written_annotations(helper):
        collect(_unquoted(annotation))
    return found


def _dotted_name(node: ast.expr) -> Optional[str]:
    """``a.b.C`` for an attribute chain rooted at a plain name, else None."""
    parts: List[str] = []
    cursor: ast.expr = node
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if not isinstance(cursor, ast.Name):
        return None
    parts.append(cursor.id)
    return ".".join(reversed(parts))


def _written_annotations(helper: ast.FunctionDef) -> List[ast.expr]:
    parameters = helper.args.posonlyargs + helper.args.args
    written = [p.annotation for p in parameters if p.annotation is not None]
    if helper.returns is not None:
        written.append(helper.returns)
    return written


def defers_annotations(module: Optional[ast.Module]) -> bool:
    """Whether the module's annotations are strings at run time rather than values."""
    return module is not None and _defers_annotations(module)


def unwritten_as_any(
    helper: ast.FunctionDef, host: Optional[ast.Module]
) -> Tuple[Tuple[str, str], ...]:
    """Write ``Any`` for each annotation that still names a whole path its host cannot reach.

    Once the caller has imported what it can under ``TYPE_CHECKING``
    (:func:`shorten_qualified_names`), a path whose head the host does not
    bind is no name there at all, so the annotation holding it is ``Any``, as
    it was before such paths were kept. A path whose head the host binds is
    left for the project check to decide. Rewrites ``helper`` in place and
    returns the ``typing`` imports that ``Any`` needs.
    """
    bound = (_import_bound_names(host) | _defined_names(host)) if host is not None else set()

    def unwritable(annotation: Optional[ast.expr]) -> bool:
        if annotation is None:
            return False
        return any(
            dotted is not None and dotted.split(".", 1)[0] not in bound
            for node in ast.walk(_unquoted(annotation))
            if isinstance(node, ast.Attribute)
            for dotted in [_dotted_name(node)]
        )

    replaced = False
    for parameter in [*helper.args.posonlyargs, *helper.args.args, *helper.args.kwonlyargs]:
        if unwritable(parameter.annotation):
            parameter.annotation, replaced = ast.Name(id="Any", ctx=ast.Load()), True
    if unwritable(helper.returns):
        helper.returns, replaced = ast.Name(id="Any", ctx=ast.Load()), True
    return typing_imports_needed(helper, host) if replaced else ()


def shorten_qualified_names(
    helper: ast.FunctionDef, replacements: Mapping[str, str], *, quote: bool = False
) -> None:
    """Rewrite each fully qualified name in place to the shorter spelling given for it.

    ``quote`` writes any annotation this touches as a string. The short name is
    reachable only through an import a checker sees and the interpreter does
    not, so unless the module defers its annotations the name would be looked
    up at definition time and not be there. A checker reads the string and is
    satisfied either way; the quotation is what keeps the module importable.
    """

    class _Shorten(ast.NodeTransformer):
        def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
            dotted = _dotted_name(node)
            shortened = replacements.get(dotted) if dotted is not None else None
            if shortened is None:
                return cast(ast.expr, self.generic_visit(node))
            return ast.copy_location(ast.Name(id=shortened, ctx=ast.Load()), node)

    def rewritten(annotation: ast.expr) -> ast.expr:
        # A string annotation is shortened inside the string, and an annotation
        # is known to be touched by what it spells, not by node identity: the
        # transformer rewrites a nested name in place and hands back the same
        # subscript, which left ``dict[Item, int]`` evaluated at definition.
        expression = _unquoted(annotation)
        was_string = expression is not annotation
        shortened = cast(ast.expr, _Shorten().visit(copy.deepcopy(expression)))
        if canonical_dump(shortened) == canonical_dump(expression):
            return annotation
        if not (quote or was_string):
            return ast.copy_location(shortened, annotation)
        return ast.copy_location(ast.Constant(value=ast.unparse(shortened)), annotation)

    for parameter in helper.args.posonlyargs + helper.args.args:
        if parameter.annotation is not None:
            parameter.annotation = rewritten(parameter.annotation)
    if helper.returns is not None:
        helper.returns = rewritten(helper.returns)


def typing_imports_needed(
    helper: ast.FunctionDef, host: Optional[ast.Module]
) -> Tuple[Tuple[str, str], ...]:
    """``("typing", name)`` for each typing name the helper's annotations use that the host lacks."""
    parameters = helper.args.posonlyargs + helper.args.args
    written = [p.annotation for p in parameters if p.annotation is not None]
    if helper.returns is not None:
        written.append(helper.returns)
    used: Set[str] = set()
    for annotation in written:
        used |= _referenced_names(_unquoted(annotation)) & _TYPING_NAMES
    bound = _import_bound_names(host) | _defined_names(host) if host is not None else set()
    return tuple(("typing", name) for name in sorted(used - bound))


def respell_bare(
    helper: ast.FunctionDef, host: Optional[ast.Module], bare_ok: Set[str]
) -> ast.FunctionDef:
    """A copy with quoted annotations unquoted where every name is now resolvable.

    Names in ``bare_ok`` are definitions the helper will be placed after.

    Resolvable is not sufficient. Unquoting makes an expression the
    interpreter evaluates, and the quotation may be the program's own: an
    annotation written ``"Annotated[int, mark('a')]"`` in the source calls
    nothing, and unquoting it here would call ``mark`` at import where the
    program never did. So an annotation is unquoted only when evaluating it
    is inert as well as safe (:func:`_evaluates_at_runtime`).
    """
    respelled = copy.deepcopy(helper)
    resolved = _BUILTIN_NAMES | _TYPING_NAMES | bare_ok
    if host is not None:
        resolved |= _import_bound_names(host)

    def unquote(annotation: Optional[ast.expr]) -> Optional[ast.expr]:
        if not (isinstance(annotation, ast.Constant) and isinstance(annotation.value, str)):
            return annotation
        expression = _unquoted(annotation)
        if expression is annotation or not _referenced_names(expression) <= resolved:
            return annotation
        if not _evaluates_at_runtime(expression, host):
            return annotation
        return expression

    for parameter in respelled.args.posonlyargs + respelled.args.args:
        parameter.annotation = unquote(parameter.annotation)
    respelled.returns = unquote(respelled.returns)
    return respelled


def complete_with_any(
    helper: ast.FunctionDef, host: Optional[ast.Module], receiver: Optional[str] = None
) -> _InferredHelper:
    """Give every still-bare parameter, and a bare return, the annotation ``Any``.

    Applied only to a helper that already carries some annotation: a partly
    annotated signature reads as an omission and is an error under mypy's
    ``disallow-incomplete-defs``, while ``Any`` states the type is unknown.
    A helper with no annotation at all is left as it is, so unannotated code
    stays unannotated. A method's receiver is not completed: no checker asks
    for its annotation, and ``Any`` there would only discard what the class
    already states.
    """
    annotated = copy.deepcopy(helper)
    parameters = annotated.args.posonlyargs + annotated.args.args
    if annotated.returns is None and all(p.annotation is None for p in parameters):
        return _InferredHelper(annotated, ())
    for parameter in parameters:
        if parameter.arg == receiver:
            continue
        if parameter.annotation is None:
            parameter.annotation = ast.Name(id="Any", ctx=ast.Load())
    if annotated.returns is None:
        annotated.returns = ast.Name(id="Any", ctx=ast.Load())
    return _InferredHelper(annotated, typing_imports_needed(annotated, host))


def infer_missing_annotations(
    helper: ast.FunctionDef,
    sites: Sequence[ApplySite],
    host_file: str,
    return_variables: Sequence[str],
    inferrer: TypeOracle,
    bare_ok: Optional[Set[str]] = None,
    receiver: Optional[str] = None,
) -> _InferredHelper:
    """A copy of ``helper`` with its annotations completed and normalized by a type checker.

    Parameters: each bare parameter's argument expression is revealed at every
    site, at the start of its block where the call will stand (a lambda is
    left alone); the annotation is the join of the revealed types, a union
    normalized by the checker's subtype relation, and unions the sites'
    declarations already supplied are normalized the same way.

    A declaration copied from the sites is what each site's parameter was
    declared as, which is not always what the block saw: ``other: _BaseVersion``
    in a method whose block runs under ``isinstance(other, Version)``. So the
    argument of a copied parameter is revealed too, and where the join of what
    the blocks saw is a strict subtype of the copy, the helper takes that
    instead; the call stands where the narrowing holds, so every site still
    passes it. A copy that says the same in other words is kept as written.

    A method's receiver, named by ``receiver``, is not among them. Its type is
    fixed by the class the method is defined on, not by the callers that happen
    to exist: a helper on a base class is inherited by every subclass, so
    joining the two observed callers into ``A | B`` both understates its domain
    and, where the two are siblings, is a type no checker will accept, because
    an explicit receiver annotation must be a supertype of its own class.
    Neither mypy nor pyright asks for one, so it is left bare.

    Return: the helper's own return type is revealed from the block's
    ``return`` expressions, or from the returned variables just after the
    block, and joined across sites. Where the sites return the call under a
    declared return type, that type is a constraint the helper's annotation
    must satisfy for the sites to keep type-checking, so the revealed type is
    written only if the checker confirms it is a subtype of every declared
    one; failing that, a declared type that is a subtype of all the others.
    """
    annotated = copy.deepcopy(helper)
    parameters = annotated.args.posonlyargs + annotated.args.args
    bare = [
        index
        for index, parameter in enumerate(parameters)
        if parameter.annotation is None
        and parameter.arg != receiver
        and all(index < len(site.call.args) for site in sites)
    ]
    copied = [
        index
        for index, parameter in enumerate(parameters)
        if parameter.annotation is not None
        and parameter.arg != receiver
        and all(
            index < len(site.call.args) and isinstance(site.call.args[index], ast.Name)
            for site in sites
        )
    ]
    probed = bare + copied
    allowed = set(_TYPING_NAMES) | (bare_ok or set())
    host_site = next((site for site in sites if site.file_path == host_file), None)
    subtypes: _Subtypes = (
        oracle_subtypes(inferrer, host_site.file_path, host_site.source)
        if host_site is not None
        else _unknown_subtypes
    )
    declared = [site.declared_return for site in sites]
    returns_call = bool(sites) and all(isinstance(site.statement, ast.Return) for site in sites)
    want_return = bool(sites) and (annotated.returns is None or returns_call)
    if not probed and not want_return:
        return _InferredHelper(annotated, ())
    requests: List[RevealRequest] = []
    return_probes: List[Tuple[str, int, int]] = []
    for site in sites:
        if probed:
            requests.append(
                RevealRequest(
                    site.file_path,
                    site.source,
                    site.start_line,
                    site.indent,
                    tuple(ast.unparse(site.call.args[index]) for index in probed),
                )
            )
        if want_return:
            for line, indent, expressions in _return_probes(site, return_variables):
                requests.append(
                    RevealRequest(site.file_path, site.source, line, indent, expressions)
                )
                return_probes.append((site.file_path, line, len(expressions)))
    revealed = inferrer.reveal(requests)
    if debugging(TYPES):
        for key, text in sorted(revealed.items()):
            TYPES.debug("%s:%d#%d -> %r", key[0].rsplit("/", 1)[-1], key[1], key[2], text)
    host = next((ast.parse(site.source) for site in sites if site.file_path == host_file), None)
    same_module = all(site.file_path == host_file for site in sites)
    for position, index in enumerate(bare):
        texts = [
            _as_class_object(site, index, revealed.get((site.file_path, site.start_line, position)))
            for site in sites
        ]
        loosened = [_as_builtin_object(site, index, text) for site, text in zip(sites, texts)]
        parameters[index].annotation = _joined_revealed(
            texts, host, same_module, subtypes, allowed, fallbacks=loosened
        )
    for position, index in enumerate(copied, start=len(bare)):
        seen = _joined_revealed(
            [revealed.get((site.file_path, site.start_line, position)) for site in sites],
            host,
            same_module,
            subtypes,
            allowed,
        )
        current = parameters[index].annotation
        if seen is not None and current is not None:
            parameters[index].annotation = _narrower(current, seen, subtypes)
    for index, parameter in enumerate(parameters):
        if index not in bare and parameter.annotation is not None:
            parameter.annotation = _renormalized(
                parameter.annotation, host, same_module, subtypes, allowed
            )
    if want_return:
        texts = [
            revealed.get((path, line, offset))
            for path, line, count in return_probes
            for offset in range(count)
        ]
        revealed_return: Optional[ast.expr] = None
        if return_variables and len(return_variables) > 1:
            revealed_return = _joined_tuple(
                texts, len(return_variables), host, same_module, subtypes, allowed
            )
        elif texts:
            revealed_return = _joined_revealed(texts, host, same_module, subtypes, allowed)
        if returns_call and any(d is not None for d in declared):
            annotated.returns = _return_under_declarations(
                revealed_return, declared, host, same_module, subtypes
            )
        elif revealed_return is not None or annotated.returns is None:
            annotated.returns = revealed_return
    return _InferredHelper(annotated, typing_imports_needed(annotated, host))


def _narrower(declared: ast.expr, seen: ast.expr, subtypes: _Subtypes) -> ast.expr:
    """``seen`` where the checker confirms it is a strict subtype of ``declared``; else ``declared``."""
    if canonical_dump(_unquoted(seen)) == canonical_dump(_unquoted(declared)):
        return declared
    narrow, wide = _unquoted(seen), _unquoted(declared)
    verdicts = list(subtypes([(narrow, wide), (wide, narrow)]))
    if len(verdicts) == 2 and verdicts[0] is Subtyping.YES and verdicts[1] is not Subtyping.YES:
        return seen
    return declared


def _return_under_declarations(
    revealed: Optional[ast.expr],
    declared: Sequence[Optional[ast.expr]],
    host: Optional[ast.Module],
    same_module: bool,
    subtypes: _Subtypes,
) -> Optional[ast.expr]:
    """The helper's return type given that every site returns it under a declared type.

    The revealed type is the most precise statement of what the helper
    returns, and it keeps every site type-checking exactly when it is a
    subtype of each declared type, which the checker confirms. Otherwise the
    declared types' own greatest lower bound, when one of them is it.
    """
    constraints = [d for d in declared if d is not None]
    if revealed is not None and len(constraints) == len(declared):
        verdicts = subtypes([(_unquoted(revealed), _unquoted(d)) for d in constraints])
        if all(verdict is Subtyping.YES for verdict in verdicts):
            return revealed
    return _met(declared, host, same_module, subtypes)


def _renormalized(
    annotation: ast.expr,
    host: Optional[ast.Module],
    same_module: bool,
    subtypes: _Subtypes,
    allowed: Set[str],
) -> ast.expr:
    """A copied union annotation with subsumed members dropped; anything else unchanged."""
    members = _union_or_optional_members(_unquoted(annotation))
    if len(members) < 2:
        return annotation
    kept = normalize_union(members, subtypes)
    if len(kept) == len(members):
        return annotation  # nothing subsumed: keep the spelling the site used
    joined = _joined(kept, host, same_module, allowed, subtypes)
    return joined if joined is not None else annotation


def _unquoted(annotation: ast.expr) -> ast.expr:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return annotation
    return annotation


def _assigned_names(statement: ast.stmt) -> Optional[Tuple[str, ...]]:
    """The names a generated ``x = helper()`` or ``x, y = helper()`` binds, in order."""
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return None
    target = statement.targets[0]
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, ast.Tuple) and all(isinstance(elt, ast.Name) for elt in target.elts):
        return tuple(elt.id for elt in target.elts if isinstance(elt, ast.Name))
    return None


def _return_probes(
    site: ApplySite, return_variables: Sequence[str]
) -> List[Tuple[int, str, Tuple[str, ...]]]:
    """Where to reveal what the helper will return: (line, indent, expressions).

    The returned variables are probed under the site's own spelling, the
    names its generated assignment binds, which alpha-renaming may spell
    differently from the helper's.
    """
    lines = source_lines(site.source)
    if return_variables:
        after = site.end_line + 1
        if after > len(lines):
            return []
        return [(after, site.indent, _assigned_names(site.statement) or tuple(return_variables))]
    if not isinstance(site.statement, ast.Return):
        return []
    block_source = "".join(lines[site.start_line - 1 : site.end_line])
    try:
        block = ast.parse(textwrap.dedent(block_source))
    except SyntaxError:
        return []
    probes: List[Tuple[int, str, Tuple[str, ...]]] = []
    for node in ast.walk(block):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return) and node.value is not None:
            line = site.start_line + node.lineno - 1
            indent = lines[line - 1][: len(lines[line - 1]) - len(lines[line - 1].lstrip())]
            probes.append((line, indent, (ast.unparse(node.value),)))
    return probes


def class_object_revealed(expression: str, revealed: str) -> str:
    """``revealed``, spelled ``type[C]`` where it is the class ``expression`` names.

    A checker shows a reference to a class as the signature of its constructor:
    ``reveal_type(ASTClass)`` answers ``def (name: str, ...) -> ASTClass``, the
    same shape it uses for an ordinary function. Written down, that says the
    parameter takes something callable, and a class passed to ``isinstance`` or
    to a ``type[T]`` parameter is then rejected. A value whose declared type is
    ``type[C]`` is shown as ``type[C]``, so only a literal reference misleads.

    Nothing in the revealed text distinguishes the two, but the expression that
    was probed does: a class is named by the class, so its last component is
    the constructed type's own name. A function whose name happens to match its
    return type's would be rewritten wrongly, and the project check that
    follows rejects it, leaving the signature as it was. A thunk that returns
    a class, ``lambda: ASTClass``, is shown as a thunk returning the
    constructor, ``def () -> def (name: str) -> ASTClass``, and is spelled
    ``def () -> type[ASTClass]`` the same way: rich's markdown elements pass
    their child classes so, to ``isinstance``.
    """
    try:
        probed = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return revealed
    thunk = "def () -> "
    if (
        isinstance(probed, ast.Lambda)
        and not probed.args.args
        and not probed.args.posonlyargs
        and not probed.args.kwonlyargs
        and probed.args.vararg is None
        and probed.args.kwarg is None
        and revealed.startswith(thunk)
    ):
        body = _dotted_name(probed.body)
        constructed = _constructed_class(revealed[len(thunk) :])
        if body is not None and constructed is not None and _names_class(body, constructed):
            return f"{thunk}type[{constructed}]"
        return revealed
    name = _dotted_name(probed)
    constructed = _constructed_class(revealed)
    if name is None or constructed is None or not _names_class(name, constructed):
        return revealed
    return f"type[{constructed}]"


def _names_class(expression: str, constructed: str) -> bool:
    """Whether a dotted ``expression`` names the class its constructor makes: the same last name."""
    return constructed.rsplit(".", 1)[-1] == expression.rsplit(".", 1)[-1]


def _constructed_class(revealed: str) -> Optional[str]:
    """The class a constructor signature makes, when every signature of it makes one class.

    ``def (...) -> C``, or mypy's ``Overload(def (...) -> C, ...)`` for a class
    whose ``__init__`` is overloaded. A named tuple's constructor makes the
    class, however mypy writes its fields.
    """
    text = revealed.strip()
    if text.startswith("Overload(") and text.endswith(")"):
        signatures = _split_top_level(text[len("Overload(") : -1])
    else:
        signatures = [text]
    made: Set[str] = set()
    for signature in signatures:
        if not signature.startswith("def ("):
            return None
        parsed = parse_revealed(signature)
        if not (
            isinstance(parsed, ast.Subscript)
            and _dotted_name(parsed.value) == "Callable"
            and isinstance(parsed.slice, ast.Tuple)
            and len(parsed.slice.elts) == 2
        ):
            return None
        constructed = _dotted_name(parsed.slice.elts[1])
        if constructed is None:
            return None
        made.add(constructed)
    return made.pop() if len(made) == 1 else None


def builtin_object_revealed(expression: str, revealed: str) -> str:
    """``revealed`` loosened so an annotation can say it, where ``expression`` is spelled as a builtin.

    Only ``parameterize_builtins`` passes a builtin to a helper, and a
    builtin's signature is written in terms the helper's module seldom can
    name: typeshed's protocols (``def (typing.Sized) -> int`` for ``len``)
    or an ``Overload(...)`` of several (``print``, ``str``). The helper calls
    what it is given, so the return type is what its body needs. A class
    whose every constructor makes it is written ``type[str]``; a callable
    whose overloads all return one type, or whose parameters name types the
    host may lack, ``Callable[..., int]``; a signature over builtins alone
    stays as it is (``Callable[[object], str]`` for ``repr``). Overloads
    returning different types (``open``, ``sorted``) are left for
    ``annotation_from_revealed`` to decline. It is a fallback: the checker's
    own spelling is used wherever it can be written, so a local of the site
    that is spelled as a builtin loses no precision to it.
    """
    if expression not in _BUILTIN_NAMES:
        return revealed
    text = revealed.strip()
    if text.startswith("Overload(") and text.endswith(")"):
        signatures = _split_top_level(text[len("Overload(") : -1])
    elif text.startswith("def "):
        signatures = [text]
    else:
        return revealed
    if not all(signature.startswith("def ") for signature in signatures):
        return revealed
    returns = [_signature_return(signature) for signature in signatures]
    if all(_generic_base(returned) == expression for returned in returns):
        return f"type[{expression}]"
    if len(set(returns)) != 1:
        return revealed
    parameters = signatures[0][: signatures[0].rfind(")")]
    if len(signatures) == 1 and "." not in parameters:
        return revealed
    return f"def (*args: Any, **kwargs: Any) -> {returns[0]}"


def _signature_return(signature: str) -> str:
    """The return type of mypy's ``def (...) -> R``; mypy leaves out ``-> None``."""
    arrow = signature.rfind(") -> ")
    return signature[arrow + len(") -> ") :].strip() if arrow >= 0 else "None"


def _generic_base(spelled: str) -> str:
    """``list`` for ``builtins.list[_T]``: the class a type is an instance of, unqualified."""
    return spelled.split("[", 1)[0].replace("builtins.", "").rsplit(".", 1)[-1]


def _as_class_object(site: ApplySite, index: int, revealed: Optional[str]) -> Optional[str]:
    """``revealed`` for the argument at ``index``, respelled when it names a class."""
    if revealed is None:
        return None
    return class_object_revealed(ast.unparse(site.call.args[index]), revealed)


def _as_builtin_object(site: ApplySite, index: int, revealed: Optional[str]) -> Optional[str]:
    """What to write for the argument at ``index`` when ``revealed`` cannot be, if it is a builtin."""
    if revealed is None:
        return None
    loosened = builtin_object_revealed(ast.unparse(site.call.args[index]), revealed)
    return loosened if loosened != revealed else None


def _joined_revealed(
    texts: Sequence[Optional[str]],
    host: Optional[ast.Module],
    same_module: bool,
    subtypes: _Subtypes = _unknown_subtypes,
    allowed: Optional[Set[str]] = None,
    fallbacks: Sequence[Optional[str]] = (),
) -> Optional[ast.expr]:
    """The normalized union of what mypy revealed at every site, when all of it can be written.

    A site's text that cannot be written is replaced by its entry in
    ``fallbacks``, when it has one (``builtin_object_revealed``).

    A site where the value is ``Any`` constrains nothing and is left out of
    the join, so long as another site says what the value is: ``Any`` is
    assignable to every parameter type, so that site's call checks whatever
    the others make the annotation, and the helper's body is checked against
    the type the rest agree on instead of none. packaging's ``Tag`` is the
    case: ``__init__`` passes ``interpreter: str`` and ``__setstate__`` the
    ``Any`` it read from a pickled dict, and the helper they share took
    ``Any`` for all three strings.
    """
    present = [text for text in texts if text is not None]
    if not present or len(present) != len(texts):
        return None
    known = [index for index, text in enumerate(present) if text.strip() != "Any"]
    if known and len(known) != len(present):
        spare_by_index = list(fallbacks) + [None] * (len(present) - len(fallbacks))
        present = [present[index] for index in known]
        fallbacks = [spare_by_index[index] for index in known]
    extra = allowed if allowed is not None else set(_TYPING_NAMES)
    spare = list(fallbacks) + [None] * (len(present) - len(fallbacks))
    candidates = [
        _written_or_fallback(
            annotation_from_revealed(text, host, same_module, extra),
            annotation_from_revealed(other, host, same_module, extra) if other else None,
            host,
        )
        for text, other in zip(present, spare)
    ]
    if any(candidate is None for candidate in candidates):
        return None
    return _joined(
        [_unquoted(c) for c in candidates if c is not None], host, same_module, extra, subtypes
    )


def _written_or_fallback(
    written: Optional[ast.expr], fallback: Optional[ast.expr], host: Optional[ast.Module]
) -> Optional[ast.expr]:
    """The checker's spelling, unless it names a whole path the host does not bind and a fallback exists.

    Such a path is imported for the checker where the host can import it, and
    written ``Any`` where it cannot (:func:`unwritten_as_any`). A fallback is
    only ever the loosened type of a builtin (``Callable[..., int]`` for
    ``len``, whose own signature names ``typing.Sized``), which is written
    as it is and says more than ``Any``.
    """
    if written is None or fallback is None:
        return written or fallback
    bound = (_import_bound_names(host) | _defined_names(host)) if host is not None else set()
    heads = _dotted_heads(_unquoted(written))
    return fallback if heads - bound else written


def _joined_tuple(
    texts: Sequence[Optional[str]],
    width: int,
    host: Optional[ast.Module],
    same_module: bool,
    subtypes: _Subtypes = _unknown_subtypes,
    allowed: Optional[Set[str]] = None,
) -> Optional[ast.expr]:
    """``tuple[...]`` of the returned variables' types, each joined across sites."""
    if len(texts) % width:
        return None
    columns = [texts[offset::width] for offset in range(width)]
    elements = [
        _joined_revealed(column, host, same_module, subtypes, allowed) for column in columns
    ]
    if any(element is None for element in elements):
        return None
    return ast.Subscript(
        value=ast.Name(id="tuple", ctx=ast.Load()),
        slice=ast.Tuple(elts=[e for e in elements if e is not None], ctx=ast.Load()),
        ctx=ast.Load(),
    )
