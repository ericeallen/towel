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

"""Preserve relationships between a helper's arguments and result.

Each original call contributes a complete signature row. Type anti-unification
operates across those rows, sharing a fresh variable for repeated disagreement
columns rather than independently joining every parameter. These are candidate
contracts, not proofs: materialization checks the helper and all consumers in
one prospective project before keeping any declaration.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, replace
from collections.abc import Iterator, Sequence
import io
import re
import textwrap
import tokenize

from .annotations import ApplySite, CallSite, _argument_annotation, _return_probes
from .models import FunctionNode, span_contains
from .type_bindings import TypeKind, TypeResolver, TypeTerm, contains_type_parameter, render_type
from .type_generalization import GenericSignature, generalize_signatures
from ..type_inference import RevealRequest, TypeOracle


@dataclass(frozen=True)
class GenericHelper:
    """An independently owned helper and the declarations committed with it."""

    helper: ast.FunctionDef
    declarations: tuple[ast.stmt, ...]


@dataclass(frozen=True)
class _ProbeKey:
    file_path: str
    line: int
    indent: str
    index: int


class _Probes:
    """Batch all expressions at a source position without aliasing reveal keys."""

    def __init__(self) -> None:
        self._requests: dict[tuple[str, int, str], tuple[str, list[str]]] = {}

    def add(self, site: ApplySite, line: int, indent: str, expression: str) -> _ProbeKey:
        key = (site.file_path, line, indent)
        _, expressions = self._requests.setdefault(key, (site.source, []))
        if expression not in expressions:
            expressions.append(expression)
        return _ProbeKey(site.file_path, line, indent, expressions.index(expression))

    def reveal(self, oracle: TypeOracle) -> dict[_ProbeKey, str]:
        """Separate probes sharing a physical line but requiring different scopes.

        Oracle keys omit indentation. A result probe just before a dedent and
        the following block's argument probe can therefore share a public key.
        Keep collector keys scoped, and use one batch unless positions collide.
        """
        batches: list[list[RevealRequest]] = []
        occurrences: dict[tuple[str, int], int] = {}
        for (path, line, indent), (source, expressions) in self._requests.items():
            position = path, line
            batch_index = occurrences.get(position, 0)
            occurrences[position] = batch_index + 1
            if batch_index == len(batches):
                batches.append([])
            batches[batch_index].append(
                RevealRequest(path, source, line, indent, tuple(expressions))
            )
        revealed: dict[_ProbeKey, str] = {}
        for batch in batches:
            result = oracle.reveal(batch)
            for request in batch:
                for index in range(len(request.expressions)):
                    value = result.get((request.file_path, request.line, index))
                    if value is not None:
                        revealed[
                            _ProbeKey(request.file_path, request.line, request.indent, index)
                        ] = value
        return revealed


@dataclass(frozen=True)
class _SiteTypes:
    resolver: TypeResolver
    arguments: tuple[TypeTerm | _ProbeKey, ...]
    declared_result: TypeTerm | None
    result_probes: tuple[tuple[_ProbeKey, ...], ...]


def _function_at(module: ast.Module, line: int) -> FunctionNode | None:
    functions = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and span_contains(node, (line, line))
    ]
    return max(functions, key=lambda function: function.lineno) if functions else None


def _site_types(
    site: ApplySite,
    host_file: str,
    host_source: str,
    return_variables: Sequence[str],
    probes: _Probes,
) -> _SiteTypes | None:
    module = ast.parse(site.source)
    function = _function_at(module, site.start_line)
    if function is None:
        return None
    resolver = TypeResolver(site.source, site.file_path, site.start_line, host_source, host_file)
    declared_site = CallSite(site.statement, site.call, function, module, site.file_path)
    arguments: list[TypeTerm | _ProbeKey] = []
    for index, argument in enumerate(site.call.args):
        annotation = _argument_annotation(declared_site, index)
        kind = resolver.resolve(annotation) if annotation is not None else None
        arguments.append(
            kind
            if kind is not None
            else probes.add(site, site.start_line, site.indent, ast.unparse(argument))
        )
    result: TypeTerm | None = None
    if isinstance(site.statement, ast.Return) and site.declared_return is not None:
        result = resolver.resolve(site.declared_return)
    elif isinstance(site.statement, ast.Expr):
        result = resolver.resolve(ast.Constant(value=None))
    result_probes = (
        tuple(
            tuple(probes.add(site, line, indent, expression) for expression in expressions)
            for line, indent, expressions in _return_probes(site, return_variables)
        )
        if result is None
        else ()
    )
    return _SiteTypes(resolver, tuple(arguments), result, result_probes)


def _tuple_type(elements: Sequence[TypeTerm], resolver: TypeResolver) -> TypeTerm | None:
    constructor = resolver.resolve(ast.Name(id="tuple", ctx=ast.Load()))
    if constructor is None:
        return None
    return TypeTerm(TypeKind.APPLY, children=(constructor, *elements))


def _signature_rows(
    sites: Sequence[ApplySite],
    host_file: str,
    host_source: str,
    return_variables: Sequence[str],
    oracle: TypeOracle,
) -> tuple[tuple[TypeTerm, ...], ...]:
    probes = _Probes()
    contexts: list[_SiteTypes] = []
    for site in sites:
        context = _site_types(site, host_file, host_source, return_variables, probes)
        if context is None:
            return ()
        contexts.append(context)
    revealed = probes.reveal(oracle)
    rows: list[tuple[TypeTerm, ...]] = []
    for context in contexts:

        def resolved(key: _ProbeKey) -> TypeTerm | None:
            text = revealed.get(key)
            return context.resolver.resolve_revealed(text) if text is not None else None

        arguments = [
            argument if isinstance(argument, TypeTerm) else resolved(argument)
            for argument in context.arguments
        ]
        result = context.declared_result
        if result is None:
            alternatives: list[TypeTerm] = []
            for keys in context.result_probes:
                elements = [resolved(key) for key in keys]
                if not elements or any(element is None for element in elements):
                    return ()
                present = [element for element in elements if element is not None]
                value = _tuple_type(present, context.resolver) if len(present) > 1 else present[0]
                if value is None:
                    return ()
                if value not in alternatives:
                    alternatives.append(value)
            # A declared return captures relationships across branches. In its
            # absence, differing branch results need a separate join proof.
            if len(alternatives) != 1:
                return ()
            result = alternatives[0]
        if any(argument is None for argument in arguments):
            return ()
        rows.append(tuple(argument for argument in arguments if argument is not None) + (result,))
    return tuple(rows)


def _reserved_names(helper: ast.FunctionDef, sources: Sequence[str]) -> set[str]:
    """Avoid capture in every site, including quoted annotations and unused binders."""
    names = {node.id for node in ast.walk(helper) if isinstance(node, ast.Name)}
    for source in sources:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.NAME:
                names.add(token.string)
            elif token.type == tokenize.STRING:
                # Forward references can mention a name absent from Python's
                # executable name nodes. Reserving all words is conservative.
                names.update(re.findall(r"\b[A-Za-z_]\w*\b", token.string))
    return names


def _quoted_type(term: TypeTerm) -> ast.Constant:
    return ast.Constant(value=ast.unparse(render_type(term)))


def _local_annotations(statements: Sequence[ast.stmt]) -> tuple[ast.AnnAssign, ...] | None:
    """Local annotations in lexical order, declining bodies with nested binders."""
    found: list[ast.AnnAssign] = []

    def visit(node: ast.AST) -> bool:
        if isinstance(node, ast.Lambda):
            return True  # Lambdas cannot contain annotations or annotation binders.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return False
        if isinstance(node, ast.AnnAssign):
            found.append(node)
        return all(visit(child) for child in ast.iter_child_nodes(node))

    return tuple(found) if all(visit(statement) for statement in statements) else None


def _annotation_structure(annotation: ast.expr) -> str:
    while isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            break
    return ast.dump(annotation)


def _signature_substitutions(
    rows: Sequence[Sequence[TypeTerm]], generalized: Sequence[TypeTerm]
) -> dict[tuple[TypeTerm, ...], TypeTerm]:
    """Relate complete source columns to the exact signature subterms they justify."""
    substitutions: dict[tuple[TypeTerm, ...], TypeTerm] = {}

    def align(column: tuple[TypeTerm, ...], target: TypeTerm) -> None:
        substitutions[column] = target
        if all(
            source.kind is target.kind and len(source.children) == len(target.children)
            for source in column
        ):
            for children, child in zip(
                zip(*(source.children for source in column)), target.children
            ):
                align(children, child)

    for column, target in zip(zip(*rows), generalized):
        align(column, target)
    return substitutions


def _generalize_annotation(
    column: tuple[TypeTerm, ...], substitutions: dict[tuple[TypeTerm, ...], TypeTerm]
) -> TypeTerm | None:
    first = column[0]
    if not contains_type_parameter(first) and all(term == first for term in column):
        return first
    if column in substitutions:
        return substitutions[column]
    if (
        any(
            term.parameter is not None
            or term.kind is not first.kind
            or len(term.children) != len(first.children)
            for term in column
        )
        or not first.children
    ):
        return None
    children = tuple(
        _generalize_annotation(values, substitutions)
        for values in zip(*(term.children for term in column))
    )
    if any(child is None for child in children):
        return None
    return replace(first, children=tuple(child for child in children if child is not None))


def _body_annotations(
    helper: ast.FunctionDef,
    sites: Sequence[ApplySite],
    rows: Sequence[Sequence[TypeTerm]],
    signature: GenericSignature,
    host_file: str,
    host_source: str,
) -> tuple[TypeTerm | None, ...] | None:
    """Prove a common rebinding for every original site's local annotation.

    Helper nodes have normalized line numbers, so they cannot identify the
    original lexical scope. Match the complete annotation sequence to an
    original span instead, and justify replacements from complete binding-aware
    columns across the sites. Identical concrete annotations stay as written.
    """
    helper_annotations = _local_annotations(helper.body)
    if helper_annotations is None:
        return None
    if not helper_annotations:
        return ()
    structure = tuple(_annotation_structure(node.annotation) for node in helper_annotations)
    represented = False
    annotations: list[tuple[TypeTerm, ...]] = []
    for site in sites:
        block = "".join(site.source.splitlines(keepends=True)[site.start_line - 1 : site.end_line])
        try:
            original = _local_annotations(ast.parse(textwrap.dedent(block)).body)
        except SyntaxError:
            return None
        if original is None or len(original) != len(helper_annotations):
            return None
        represented |= (
            tuple(_annotation_structure(node.annotation) for node in original) == structure
        )
        resolver = TypeResolver(
            site.source, site.file_path, site.start_line, host_source, host_file
        )
        terms: list[TypeTerm] = []
        for node in original:
            resolved = resolver.resolve(node.annotation)
            if resolved is None:
                return None
            terms.append(resolved)
        annotations.append(tuple(terms))
    if not represented:
        return None
    substitutions = _signature_substitutions(rows, signature.types)
    rewritten: list[TypeTerm | None] = []
    for column in zip(*annotations):
        term = _generalize_annotation(column, substitutions)
        if term is None:
            return None
        rewritten.append(term if any(original != term for original in column) else None)
    return tuple(rewritten)


def _render_generic(
    helper: ast.FunctionDef,
    signature: GenericSignature,
    typevar_alias: str,
    body_annotations: Sequence[TypeTerm | None],
) -> GenericHelper:
    annotated = copy.deepcopy(helper)
    parameters = annotated.args.posonlyargs + annotated.args.args
    for parameter, term in zip(parameters, signature.types[:-1]):
        parameter.annotation = _quoted_type(term)
    annotated.returns = _quoted_type(signature.types[-1])
    local_annotations = _local_annotations(annotated.body)
    assert local_annotations is not None
    for statement, annotation in zip(local_annotations, body_annotations):
        if annotation is not None:
            statement.annotation = _quoted_type(annotation)
    declarations: list[ast.stmt] = [
        ast.ImportFrom(
            module="typing", names=[ast.alias(name="TypeVar", asname=typevar_alias)], level=0
        )
    ]
    for binder in signature.parameters:
        arguments: list[ast.expr] = [ast.Constant(value=binder.name)]
        arguments.extend(_quoted_type(constraint) for constraint in binder.constraints)
        declarations.append(
            ast.Assign(
                targets=[ast.Name(id=binder.name, ctx=ast.Store())],
                value=ast.Call(
                    func=ast.Name(id=typevar_alias, ctx=ast.Load()),
                    args=arguments,
                    keywords=(
                        [ast.keyword(arg="bound", value=_quoted_type(binder.bound))]
                        if binder.bound is not None
                        else []
                    ),
                ),
            )
        )
    return GenericHelper(
        ast.fix_missing_locations(annotated),
        tuple(ast.fix_missing_locations(declaration) for declaration in declarations),
    )


def generic_helpers(
    helper: ast.FunctionDef,
    sites: Sequence[ApplySite],
    host_file: str,
    host_source: str,
    return_variables: Sequence[str],
    oracle: TypeOracle,
) -> Iterator[GenericHelper]:
    """Fresh generic contracts for module-level extraction, never unchecked fallbacks."""
    width = len(helper.args.posonlyargs) + len(helper.args.args)
    if len(sites) < 2 or any(len(site.call.args) != width for site in sites):
        return
    rows = _signature_rows(sites, host_file, host_source, return_variables, oracle)
    if not rows:
        return
    reserved = _reserved_names(helper, [host_source, *(site.source for site in sites)])
    alias = "_towel_typevar"
    while alias in reserved:
        alias += "_"
    reserved.add(alias)
    for signature in generalize_signatures(rows, reserved):
        body_annotations = _body_annotations(helper, sites, rows, signature, host_file, host_source)
        if body_annotations is not None:
            yield _render_generic(helper, signature, alias, body_annotations)
