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

A row is the site's own types, resolved in the site's module by the names the
program's imports give them (``module_names``), so a type only that module
can name may still be generalized away into a variable; a signature that
would have to write it where the helper is defined is not offered. A
parameter the helper's body never reads is ``object``: every argument is one,
and its type then need not be known at all. The ``typing`` names a signature
writes that the host lacks are imported with it.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, replace
from collections.abc import Iterator, Sequence
import io
import os
import re
import textwrap
import tokenize

from .annotations import (
    ApplySite,
    CallSite,
    _argument_annotation,
    _return_probes,
    class_object_revealed,
)
from .models import FunctionNode, MethodKind, span_contains
from .statement_facts import bindings_of
from .type_bindings import (
    CheckerImports,
    ModuleNames,
    TypeKind,
    TypeResolver,
    TypeTerm,
    checking_imports,
    render_type,
    required_imports,
    spellable,
    type_parameter_identities,
    unambiguous_spellings,
)
from .type_generalization import GenericSignature, aligned_children, generalize_signatures
from ..type_inference import RevealRequest, TypeOracle


@dataclass(frozen=True)
class GenericHelper:
    """An independently owned helper, the declarations committed with it, and its imports."""

    helper: ast.FunctionDef
    declarations: tuple[ast.stmt, ...]
    required_imports: tuple[tuple[str, str], ...] = ()
    type_checking_imports: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class MethodContext:
    """The lexical host and implicit receiver already chosen by helper placement."""

    host_class: str
    kind: MethodKind
    receiver_name: str | None


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
    argument_expressions: tuple[str, ...] = ()
    """What each argument probe reveals, for reading a class the site passes as a class."""


@dataclass(frozen=True)
class _Alias:
    """A returned variable bound once, to a parameter or to what calling that parameter returns."""

    parameter: int
    called: bool


def _returned_aliases(
    helper: ast.FunctionDef, return_variables: Sequence[str]
) -> tuple[_Alias | None, ...]:
    """For each returned variable, the parameter it is only ever bound from, if one.

    Such a variable holds the argument, or the thunk argument's result, at
    every site, so its type is taken from the argument's own. A reveal of the
    site's variable would answer the same type in another spelling where it
    matters most: mypy shows a class held in a variable as its constructor,
    and the row would then relate nothing to the ``type[C]`` passed in.
    """
    parameters = [parameter.arg for parameter in helper.args.posonlyargs + helper.args.args]
    bound = [bindings_of(statement, into_nested_scopes=False) for statement in helper.body]
    declared = {
        name
        for node in ast.walk(helper)
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }
    aliases: list[_Alias | None] = []
    for variable in return_variables:
        binders = [statement for statement, names in zip(helper.body, bound) if variable in names]
        alias: _Alias | None = None
        if len(binders) == 1 and variable not in declared:
            statement = binders[0]
            value = statement.value if isinstance(statement, ast.Assign) else None
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                called = isinstance(value, ast.Call) and not value.args and not value.keywords
                source = value.func if isinstance(value, ast.Call) and called else value
                if (
                    isinstance(source, ast.Name)
                    and source.id in parameters
                    and source.id not in declared
                    and not any(source.id in names for names in bound)
                ):
                    alias = _Alias(parameters.index(source.id), called)
        aliases.append(alias)
    return tuple(aliases)


def _thunk_result(term: TypeTerm) -> TypeTerm | None:
    """``R`` for ``Callable[[], R]``: what calling a thunk of that type returns."""
    if (
        term.kind is TypeKind.APPLY
        and len(term.children) == 3
        and term.children[0].name == "collections.abc.Callable"
        and term.children[1].kind is TypeKind.LIST
        and not term.children[1].children
    ):
        return term.children[2]
    return None


def _function_at(module: ast.Module, line: int) -> FunctionNode | None:
    functions = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and span_contains(node, (line, line))
    ]
    return max(functions, key=lambda function: function.lineno) if functions else None


@dataclass(frozen=True)
class _Context:
    """What every site's types are resolved against: the helper's host and the program's names."""

    host_file: str
    host_source: str
    host_class: str | None = None
    module_names: ModuleNames | None = None
    checker_imports: CheckerImports | None = None

    def resolver(self, source: str, file_path: str, line: int) -> TypeResolver:
        return TypeResolver(
            source,
            file_path,
            line,
            self.host_source,
            self.host_file,
            host_class=self.host_class,
            module_names=self.module_names,
            checker_imports=self.checker_imports,
        )


def _site_types(
    site: ApplySite,
    context: _Context,
    return_variables: Sequence[str],
    probes: _Probes,
    receiver_index: int | None = None,
    unused: frozenset[int] = frozenset(),
) -> _SiteTypes | None:
    module = ast.parse(site.source)
    function = _function_at(module, site.start_line)
    if function is None:
        return None
    resolver = context.resolver(site.source, site.file_path, site.start_line)
    declared_site = CallSite(site.statement, site.call, function, module, site.file_path)
    arguments: list[TypeTerm | _ProbeKey] = []
    expressions: list[str] = []
    for index, argument in enumerate(site.call.args):
        if index == receiver_index:
            continue
        expressions.append(ast.unparse(argument))
        if index in unused:
            top = resolver.resolve_revealed("builtins.object")
            if top is None:
                return None
            arguments.append(top)
            continue
        annotation = _argument_annotation(declared_site, index)
        kind = resolver.resolve(annotation) if annotation is not None else None
        arguments.append(
            kind
            if kind is not None
            else probes.add(site, site.start_line, site.indent, expressions[-1])
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
    return _SiteTypes(resolver, tuple(arguments), result, result_probes, tuple(expressions))


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
    *,
    host_class: str | None = None,
    receiver_index: int | None = None,
    module_names: ModuleNames | None = None,
    checker_imports: CheckerImports | None = None,
    unused: frozenset[int] = frozenset(),
    aliases: Sequence[_Alias | None] = (),
) -> tuple[tuple[TypeTerm, ...], ...]:
    context = _Context(host_file, host_source, host_class, module_names, checker_imports)
    probes = _Probes()
    contexts: list[_SiteTypes] = []
    for site in sites:
        site_types = _site_types(site, context, return_variables, probes, receiver_index, unused)
        if site_types is None:
            return ()
        contexts.append(site_types)
    revealed = probes.reveal(oracle)
    rows: list[tuple[TypeTerm, ...]] = []
    for site_types in contexts:

        def resolved(key: _ProbeKey, expression: str | None = None) -> TypeTerm | None:
            text = revealed.get(key)
            if text is not None and expression is not None:
                text = class_object_revealed(expression, text)
            return site_types.resolver.resolve_revealed(text) if text is not None else None

        arguments = [
            argument if isinstance(argument, TypeTerm) else resolved(argument, expression)
            for argument, expression in zip(site_types.arguments, site_types.argument_expressions)
        ]

        def aliased(position: int) -> TypeTerm | None:
            """The argument a returned variable holds at this site, when it holds one."""
            alias = aliases[position] if position < len(aliases) else None
            if alias is None or alias.parameter == receiver_index:
                return None
            index = alias.parameter - (
                1 if receiver_index is not None and alias.parameter > receiver_index else 0
            )
            term = arguments[index] if index < len(arguments) else None
            if term is None or not alias.called:
                return term
            return _thunk_result(term)

        result = site_types.declared_result
        if result is None:
            alternatives: list[TypeTerm] = []
            for keys in site_types.result_probes:
                elements = [
                    (aliased(position) if return_variables else None) or resolved(key)
                    for position, key in enumerate(keys)
                ]
                if not elements or any(element is None for element in elements):
                    return ()
                present = [element for element in elements if element is not None]
                value = (
                    _tuple_type(present, site_types.resolver) if len(present) > 1 else present[0]
                )
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
            columns = aligned_children(column)
            assert columns is not None
            for children, child in zip(columns, target.children):
                align(children, child)

    for column, target in zip(zip(*rows), generalized):
        align(column, target)
    return substitutions


def _generalize_annotation(
    column: tuple[TypeTerm, ...],
    substitutions: dict[tuple[TypeTerm, ...], TypeTerm],
    retained_parameters: frozenset[str],
) -> TypeTerm | None:
    first = column[0]
    if type_parameter_identities(first) <= retained_parameters and all(
        term == first for term in column
    ):
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
    columns = aligned_children(column)
    assert columns is not None  # same kind and arity, checked above
    children = tuple(
        _generalize_annotation(values, substitutions, retained_parameters) for values in columns
    )
    if any(child is None for child in children):
        return None
    return replace(first, children=tuple(child for child in children if child is not None))


def _body_annotations(
    helper: ast.FunctionDef,
    sites: Sequence[ApplySite],
    rows: Sequence[Sequence[TypeTerm]],
    signature: GenericSignature,
    context: _Context,
    retained_parameters: frozenset[str] = frozenset(),
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
        resolver = context.resolver(site.source, site.file_path, site.start_line)
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
        term = _generalize_annotation(column, substitutions, retained_parameters)
        if term is None:
            return None
        rewritten.append(term if any(original != term for original in column) else None)
    return tuple(rewritten)


def _written_terms(
    signature: GenericSignature, body_annotations: Sequence[TypeTerm | None]
) -> tuple[TypeTerm, ...]:
    """Every term a rendered signature writes: its types, its binders' domains, local annotations."""
    domains = tuple(
        term
        for binder in signature.parameters
        for term in ((binder.bound,) if binder.bound is not None else ()) + binder.constraints
    )
    return (
        signature.types
        + domains
        + tuple(annotation for annotation in body_annotations if annotation is not None)
    )


def _render_generic(
    helper: ast.FunctionDef,
    signature: GenericSignature,
    typevar_alias: str,
    body_annotations: Sequence[TypeTerm | None],
    receiver_index: int | None = None,
) -> GenericHelper:
    annotated = copy.deepcopy(helper)
    parameters = annotated.args.posonlyargs + annotated.args.args
    if receiver_index is not None:
        parameters[receiver_index].annotation = None
        parameters = [
            parameter for index, parameter in enumerate(parameters) if index != receiver_index
        ]
    for parameter, term in zip(parameters, signature.types[:-1]):
        parameter.annotation = _quoted_type(term)
    annotated.returns = _quoted_type(signature.types[-1])
    local_annotations = _local_annotations(annotated.body)
    assert local_annotations is not None
    for statement, annotation in zip(local_annotations, body_annotations):
        if annotation is not None:
            statement.annotation = _quoted_type(annotation)
    declarations: list[ast.stmt] = []
    if signature.parameters:
        declarations.append(
            ast.ImportFrom(
                module="typing", names=[ast.alias(name="TypeVar", asname=typevar_alias)], level=0
            )
        )
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
    written = _written_terms(signature, body_annotations)
    return GenericHelper(
        ast.fix_missing_locations(annotated),
        tuple(ast.fix_missing_locations(declaration) for declaration in declarations),
        required_imports(written),
        checking_imports(written),
    )


def _unused_parameters(helper: ast.FunctionDef) -> frozenset[int]:
    """Positions of the parameters the helper's body never names."""
    used = {
        node.id
        for statement in helper.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    }
    parameters = helper.args.posonlyargs + helper.args.args
    return frozenset(
        index for index, parameter in enumerate(parameters) if parameter.arg not in used
    )


def generic_helpers(
    helper: ast.FunctionDef,
    sites: Sequence[ApplySite],
    host_file: str,
    host_source: str,
    return_variables: Sequence[str],
    oracle: TypeOracle,
    *,
    method: MethodContext | None = None,
    module_names: ModuleNames | None = None,
    checker_imports: CheckerImports | None = None,
) -> Iterator[GenericHelper]:
    """Generic contracts preserving host binders, never unchecked fallbacks.

    ``module_names`` gives the absolute name the program's imports give the
    module at a path, which is how the checker names the types it reveals;
    ``checker_imports`` how the host may import a class it cannot yet name.
    Sites in different modules whose types all agree need no variable, and
    their common signature is offered as it is: it names the types by what
    they are, where the ordinary rung, spelling across modules, can write
    only builtins. Within one module the ordinary rung writes the same types.
    """
    parameters = helper.args.posonlyargs + helper.args.args
    width = len(parameters)
    if len(sites) < 2 or any(len(site.call.args) != width for site in sites):
        return
    host_class = method.host_class if method is not None else None
    retained: frozenset[str] = frozenset()
    receiver_index = None
    if method is not None and method.kind != "staticmethod":
        receiver_index = next(
            (
                index
                for index, parameter in enumerate(parameters)
                if parameter.arg == method.receiver_name
            ),
            None,
        )
        for site in sites:
            function = _function_at(ast.parse(site.source), site.start_line)
            if function is None:
                return
            positional = function.args.posonlyargs + function.args.args
            # An explicit self/cls contract may restrict dispatch to a subset
            # of the class. Do not silently replace it by the inferred host.
            if not positional or positional[0].annotation is not None:
                return
            if receiver_index is not None:
                receiver = site.call.args[receiver_index]
                if not isinstance(receiver, ast.Name) or receiver.id != positional[0].arg:
                    return
    context = _Context(host_file, host_source, host_class, module_names, checker_imports)
    if method is not None and method.kind != "staticmethod":
        retained = context.resolver(host_source, host_file, 1).host_class_parameter_identities
    rows = _signature_rows(
        sites,
        host_file,
        host_source,
        return_variables,
        oracle,
        host_class=host_class,
        receiver_index=receiver_index,
        module_names=module_names,
        checker_imports=checker_imports,
        unused=_unused_parameters(helper) - {receiver_index},
        aliases=_returned_aliases(helper, return_variables),
    )
    if not rows:
        return
    reserved = _reserved_names(helper, [host_source, *(site.source for site in sites)])
    alias = "_towel_typevar"
    while alias in reserved:
        alias += "_"
    reserved.add(alias)
    signatures = generalize_signatures(
        rows,
        reserved,
        retained_parameters=retained if method is not None else frozenset(),
        constrainable=spellable,
    ) or (
        _common_signature(rows, retained)
        if any(os.path.abspath(site.file_path) != os.path.abspath(host_file) for site in sites)
        else ()
    )
    for signature in signatures:
        body_annotations = _body_annotations(helper, sites, rows, signature, context, retained)
        if body_annotations is None:
            continue
        written = _written_terms(signature, body_annotations)
        if all(spellable(term) for term in written) and unambiguous_spellings(written):
            yield _render_generic(helper, signature, alias, body_annotations, receiver_index)


def _common_signature(
    rows: Sequence[Sequence[TypeTerm]], retained: frozenset[str]
) -> tuple[GenericSignature, ...]:
    """The one signature every row states, when they all state the same one."""
    first = tuple(rows[0]) if rows else ()
    if (
        not first
        or any(tuple(row) != first for row in rows[1:])
        or any(not type_parameter_identities(term) <= retained for term in first)
    ):
        return ()
    return (GenericSignature(first, ()),)
