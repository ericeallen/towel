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
same module, a name bound by a module-level import; and a subscripted
annotation only when the subscript evaluates at definition time
(:func:`_evaluates_at_runtime`). Otherwise it is a string, which never
evaluates and which type checkers resolve in the module. Across modules only
builtin names and the caller's ``typing`` names are used, since a site's
imports are not the host's.

Helpers are annotated only in code that already uses annotations somewhere
among the sites, so an unannotated project stays that way.
"""

from __future__ import annotations

import ast
import builtins
import copy
import textwrap
from dataclasses import dataclass
import re
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

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
        Subtyping.YES if ast.dump(narrow) == ast.dump(wide) else Subtyping.UNKNOWN
        for narrow, wide in pairs
    ]


def oracle_subtypes(oracle: TypeOracle, file_path: str, source: str) -> _Subtypes:
    """The relation as the type checker judges it in ``file_path``.

    A pair the checker cannot judge stays None, which every caller treats as
    "not known to be a subtype".
    """

    def relation(pairs: Sequence[Tuple[ast.expr, ast.expr]]) -> Sequence[Subtyping]:
        if not pairs:
            return []
        return list(
            oracle.is_subtype(
                file_path, source, [(ast.unparse(n), ast.unparse(w)) for n, w in pairs]
            )
        )

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
    if all(ast.dump(candidate) == ast.dump(first) for candidate in present[1:]):
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
        key = ast.dump(member)
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
        return None
    if names - resolved <= _import_bound_names(host) and _evaluates_at_runtime(expression, host):
        return expression
    return annotation if quoted else ast.Constant(value=ast.unparse(expression))


_RUNTIME_GENERICS = frozenset({"list", "dict", "set", "frozenset", "tuple", "type"})
_TYPING_MODULES = frozenset({"typing", "typing_extensions", "collections.abc"})


def _evaluates_at_runtime(expression: ast.expr, host: Optional[ast.Module]) -> bool:
    """Whether the annotation can be evaluated where the helper is defined.

    Every name resolving is not enough: ``memoryview[int]`` resolves and
    raises ``TypeError`` at definition time on interpreters where
    ``memoryview`` is not generic (tornado). A subscript is trusted only on a
    PEP 585 builtin generic, on a name imported from ``typing`` or
    ``collections.abc``, or on ``typing.X`` with ``typing`` imported; every
    other subscripted annotation is written as a string, which is never
    evaluated and which checkers resolve the same way.
    """
    generic_names = set(_RUNTIME_GENERICS) | {"Any", "Callable"}
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


_LITERAL = re.compile(r"Literal\[(?P<value>[^\]]*)\]\??")
_CALLABLE = re.compile(r"^(?:def )?\((?P<params>.*)\) -> (?P<returns>.+)$")
_TYPING_NAMES = frozenset({"Any", "Callable"})
"""Names an inferred annotation may use that the host must import from ``typing``."""


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


def _callable_spelling(text: str) -> Optional[str]:
    """``Callable[...]`` for mypy's ``def (...) -> R`` spelling of a callable.

    Plain positional parameters become ``Callable[[T1, T2], R]``; anything with
    defaults, ``*args``, ``**kwargs`` or keyword-only parameters becomes
    ``Callable[..., R]``.
    """
    match = _CALLABLE.match(text)
    if match is None:
        return None
    params = _split_top_level(match.group("params"))
    returns = match.group("returns").strip()
    if not params:
        return f"Callable[[], {returns}]"
    types: List[str] = []
    for param in params:
        if param.startswith("*") or "=" in param:
            return f"Callable[..., {returns}]"
        name, colon, kind = param.partition(":")
        types.append(kind.strip() if colon else name.strip())
    return f"Callable[[{', '.join(types)}], {returns}]"


def annotation_from_revealed(
    revealed: str,
    host: Optional[ast.Module],
    same_module: bool,
    bare_ok: Optional[Set[str]] = None,
) -> Optional[ast.expr]:
    """An annotation from mypy's spelling of a type, or None when it cannot be written.

    Inferred-literal markers (``Literal['x']?``) become the literal's builtin
    type and ``builtins.``/``?``/``*`` markers are dropped. Anything containing
    ``Any``, a callable, or an unresolvable name is declined; a dotted name is
    kept only when its head is bound in the host, or reduced to its last part
    when that is bound there.
    """
    text = revealed.strip()
    if not text or "<" in text or "Never" in text:
        return None
    if text == "Any":
        return None  # what an unannotated parameter already means
    if text.startswith("def ") or text.startswith("("):
        spelled = _callable_spelling(text)
        if spelled is None:
            return None
        text = spelled

    def literal_type(match: "re.Match[str]") -> str:
        try:
            value = ast.literal_eval(match.group("value"))
        except (ValueError, SyntaxError):
            return "Any"
        kind = _literal_type(value)
        return kind.id if isinstance(kind, ast.Name) else "Any"

    text = _LITERAL.sub(literal_type, text)
    text = text.replace("builtins.", "").replace("?", "").replace("*", "")
    try:
        expression = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None
    if not _is_type_expression(expression):
        return None
    reduced = _reduce_dotted_names(expression, host)
    if reduced is None:
        return None
    return _spelled_for_host(reduced, host, same_module, set(_TYPING_NAMES) | (bare_ok or set()))


def _is_type_expression(expression: ast.expr) -> bool:
    """Whether the tree is made only of what a type annotation is made of.

    Names, attributes, subscripts, tuples, constants, and ``|`` unions. A
    module name that is not an identifier, for example, parses as arithmetic.
    """
    for node in ast.walk(expression):
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, ast.BitOr):
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
            ),
        ):
            return False
    return True


def _reduce_dotted_names(expression: ast.expr, host: Optional[ast.Module]) -> Optional[ast.expr]:
    """Rewrite ``pkg.mod.Name`` to what the host can spell, or None if it cannot."""
    bound = (
        _import_bound_names(host) | _defined_names(host) if host is not None else set()
    ) | _TYPING_NAMES
    reducer = _DottedNameReducer(bound)
    result = reducer.visit(copy.deepcopy(expression))
    return None if reducer.failed else result


class _DottedNameReducer(ast.NodeTransformer):
    """Rewrite each ``pkg.mod.Name`` to a name in ``bound``; ``failed`` when one cannot be."""

    def __init__(self, bound: Set[str]) -> None:
        self.bound = bound
        self.failed = False

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        head: ast.expr = node
        while isinstance(head, ast.Attribute):
            head = head.value
        if isinstance(head, ast.Name) and head.id in self.bound:
            return node  # ``typing.Sequence`` with ``import typing`` in the host
        if node.attr in self.bound:
            return ast.Name(id=node.attr, ctx=ast.Load())
        self.failed = True
        return node


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


def complete_with_any(helper: ast.FunctionDef, host: Optional[ast.Module]) -> _InferredHelper:
    """Give every still-bare parameter, and a bare return, the annotation ``Any``.

    Applied only to a helper that already carries some annotation: a partly
    annotated signature reads as an omission and is an error under mypy's
    ``disallow-incomplete-defs``, while ``Any`` states the type is unknown.
    A helper with no annotation at all is left as it is, so unannotated code
    stays unannotated.
    """
    annotated = copy.deepcopy(helper)
    parameters = annotated.args.posonlyargs + annotated.args.args
    if annotated.returns is None and all(p.annotation is None for p in parameters):
        return _InferredHelper(annotated, ())
    for parameter in parameters:
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
) -> _InferredHelper:
    """A copy of ``helper`` with its annotations completed and normalized by a type checker.

    Parameters: each bare parameter's argument expression is revealed at every
    site, at the start of its block where the call will stand (a lambda is
    left alone); the annotation is the join of the revealed types, a union
    normalized by the checker's subtype relation, and unions the sites'
    declarations already supplied are normalized the same way.

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
        if parameter.annotation is None and all(index < len(site.call.args) for site in sites)
    ]
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
    if not bare and not want_return:
        return _InferredHelper(annotated, ())
    requests: List[RevealRequest] = []
    return_probes: List[Tuple[str, int, int]] = []
    for site in sites:
        if bare:
            requests.append(
                RevealRequest(
                    site.file_path,
                    site.source,
                    site.start_line,
                    site.indent,
                    tuple(ast.unparse(site.call.args[index]) for index in bare),
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
        texts = [revealed.get((site.file_path, site.start_line, position)) for site in sites]
        parameters[index].annotation = _joined_revealed(texts, host, same_module, subtypes, allowed)
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


def _joined_revealed(
    texts: Sequence[Optional[str]],
    host: Optional[ast.Module],
    same_module: bool,
    subtypes: _Subtypes = _unknown_subtypes,
    allowed: Optional[Set[str]] = None,
) -> Optional[ast.expr]:
    """The normalized union of what mypy revealed at every site, when all of it can be written."""
    present = [text for text in texts if text is not None]
    if not present or len(present) != len(texts):
        return None
    extra = allowed if allowed is not None else set(_TYPING_NAMES)
    candidates = [annotation_from_revealed(text, host, same_module, extra) for text in present]
    if any(candidate is None for candidate in candidates):
        return None
    return _joined(
        [_unquoted(c) for c in candidates if c is not None], host, same_module, extra, subtypes
    )


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
