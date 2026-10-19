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

"""Original-source certificates for fresh local outputs inside an IF-only suite."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, FrozenSet, Iterator, Literal, Optional, Sequence, Set, Tuple, Union

from ..canonical_ast import exact_dump
from ..analysis_sources import ParserConfiguration, parser_configuration, parsing_is_pure
from .bounded_cache import BoundedCache, memoizing
from .assignment_analyzer import own_scope_bindings
from .ast_paths import Path, path_map, resolve_path
from .definite_assignment import definitely_bound_after
from .models import FunctionNode, source_digest_of
from .parameters import parameter_names

SuiteField = Literal["body", "orelse"]
IfSuiteStep = Tuple[SuiteField, int]


@dataclass(frozen=True)
class IfTransferSite:
    module_digest: str
    function_position: Tuple[int, int]
    block_position: Tuple[int, int, int]


@dataclass(frozen=True)
class IfBindingTransfer:
    """Immutable source locations and binding facts; no AST or owner survives in this value."""

    site: IfTransferSite
    function_path: Path
    suite_path: Tuple[IfSuiteStep, ...]
    suite_field: SuiteField
    start: int
    count: int
    initialized_names: FrozenSet[str]
    normal_exit_bound: FrozenSet[str]
    returned_names: Tuple[str, ...]


def _unique_tree(tree: ast.AST) -> bool:
    """A fresh graph check before local visitors; marker sharing alone is harmless."""
    seen: Set[int] = set()
    pending: list[ast.AST] = [tree]
    markers = (ast.operator, ast.unaryop, ast.boolop, ast.cmpop, ast.expr_context)
    while pending:
        node = pending.pop()
        children = tuple(ast.iter_child_nodes(node))
        if isinstance(node, markers):
            if children:
                return False
            continue
        if id(node) in seen:
            return False
        seen.add(id(node))
        pending.extend(children)
    return True


def _source_corresponds(module: ast.Module, source: str) -> bool:
    proofs = _CORRESPONDENCE_PROOFS.get()
    if proofs is not None and memoizing() and parsing_is_pure(source):
        return proofs.corresponds(module, source)
    return _source_corresponds_uncached(module, source)


def _source_corresponds_uncached(module: ast.Module, source: str) -> bool:
    if not _unique_tree(module):
        return False
    try:
        actual = exact_dump(module)
        cache = _cache_for(source)
        if cache is not None:
            original = cache.original(source)
            if original is not None:
                return any(actual == variant.spelling for variant in original.variants)
        return any(
            actual == exact_dump(ast.parse(source, type_comments=comments))
            for comments in (False, True)
        )
    except (SyntaxError, ValueError, RecursionError):
        return False


@dataclass(frozen=True)
class _CorrespondenceProof:
    configuration: ParserConfiguration
    fingerprint: Optional[str]


class _CorrespondenceProofs:
    """Successful proofs for explicitly owned, immutable trees in one analysis."""

    def __init__(
        self, modules: Sequence[Tuple[ast.Module, str]], check_ast_immutable: bool
    ) -> None:
        self.owners = dict(modules)
        self.proofs: Dict[ast.Module, _CorrespondenceProof] = {}
        self.check_ast_immutable = check_ast_immutable

    def corresponds(self, module: ast.Module, source: str) -> bool:
        if self.owners.get(module) != source:
            return _source_corresponds_uncached(module, source)
        configuration = parser_configuration()
        known = self.proofs.get(module)
        if known is not None and known.configuration == configuration:
            if self.check_ast_immutable and (
                not _unique_tree(module) or exact_dump(module) != known.fingerprint
            ):
                raise RuntimeError("Analysis mutated an owned IF source-correspondence tree")
            return True
        if not _source_corresponds_uncached(module, source):
            return False
        self.proofs[module] = _CorrespondenceProof(
            configuration, exact_dump(module) if self.check_ast_immutable else None
        )
        return True


_CORRESPONDENCE_PROOFS: ContextVar[Optional[_CorrespondenceProofs]] = ContextVar(
    "if_transfer_correspondence_proofs", default=None
)


@contextmanager
def sharing_if_transfer_proofs(
    modules: Sequence[Tuple[ast.Module, str]], *, check_ast_immutable: bool = False
) -> Iterator[None]:
    """Trust only the analysis's own read-only trees; caller trees are always re-proved."""
    proofs = _CorrespondenceProofs(modules, check_ast_immutable)
    token = _CORRESPONDENCE_PROOFS.set(proofs)
    try:
        yield
    finally:
        _CORRESPONDENCE_PROOFS.reset(token)
        proofs.owners.clear()
        proofs.proofs.clear()


def _selected_node_ids(nodes: Sequence[ast.stmt]) -> Optional[FrozenSet[int]]:
    """Only an early refusal: safely scan the selected tree before source proof."""
    forbidden = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Lambda,
        ast.Global,
        ast.Nonlocal,
        ast.NamedExpr,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    markers = (ast.operator, ast.unaryop, ast.boolop, ast.cmpop, ast.expr_context)
    seen: Set[int] = set()
    pending: list[ast.AST] = list(nodes)
    while pending:
        node = pending.pop()
        if isinstance(node, forbidden):
            return None
        if isinstance(node, markers):
            if tuple(ast.iter_child_nodes(node)):
                return None
            continue
        if id(node) in seen:
            return None
        seen.add(id(node))
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(seen)


def _suite_location(
    statements: Sequence[ast.stmt],
    nodes: Sequence[ast.stmt],
) -> Optional[Tuple[Tuple[IfSuiteStep, ...], SuiteField, int]]:
    """Cheap IF-only eligibility; source ownership is still proved separately."""
    pending: list[Tuple[Sequence[ast.stmt], Tuple[IfSuiteStep, ...], SuiteField]] = [
        (statements, (), "body")
    ]
    seen: Set[int] = set()
    while pending:
        suite, path, field = pending.pop()
        for index, statement in enumerate(suite):
            if id(statement) in seen:
                return None
            seen.add(id(statement))
            if statement is nodes[0]:
                selected = suite[index : index + len(nodes)]
                if (
                    path
                    and len(selected) == len(nodes)
                    and all(a is b for a, b in zip(selected, nodes))
                ):
                    return path, field, index
            if isinstance(statement, ast.If):
                inner = path + ((field, index),)
                pending.append((statement.orelse, inner, "orelse"))
                pending.append((statement.body, inner, "body"))
    return None


def _fresh_flow(
    statements: Sequence[ast.stmt],
    names: FrozenSet[str],
    definite: FrozenSet[str] = frozenset(),
    possible: FrozenSet[str] = frozenset(),
) -> Optional[FrozenSet[str]]:
    """Reject reads before initialization and repeated writes on an IF-only path.

    Definite normal-exit facts come from the existing assignment analysis;
    the possible set only prevents a later write from replacing any earlier
    branch's initialization. Alternative arms start with the same entry facts.
    """
    for statement in statements:
        expression: Optional[ast.expr]
        if isinstance(statement, ast.If):
            expression = statement.test
            targets: Set[str] = set()
        elif isinstance(statement, ast.Assign) and all(
            isinstance(t, ast.Name) for t in statement.targets
        ):
            expression = statement.value
            targets = {t.id for t in statement.targets if isinstance(t, ast.Name)}
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            expression = statement.value
            targets = {statement.target.id} if expression is not None else set()
        elif isinstance(statement, ast.Expr):
            expression = statement.value
            targets = set()
        elif isinstance(statement, ast.Pass):
            continue
        else:
            return None
        if (
            targets & possible
            or expression is not None
            and any(
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in names
                and node.id not in definite
                for node in ast.walk(expression)
            )
        ):
            return None
        if isinstance(statement, ast.If):
            arms = [
                _fresh_flow(arm, names, definite, possible)
                for arm in (statement.body, statement.orelse)
            ]
            if any(arm is None for arm in arms):
                return None
            possible = possible | frozenset().union(*(arm for arm in arms if arm is not None))
        else:
            possible = possible | targets
        after = definitely_bound_after((statement,))
        if after is None:
            return None
        definite = definite | frozenset(after)
    return possible


@dataclass(frozen=True)
class _LocalTransfer:
    location: Tuple[Tuple[IfSuiteStep, ...], SuiteField, int]
    initialized_names: FrozenSet[str]
    normal_exit_bound: FrozenSet[str]


@dataclass(frozen=True)
class _ParsedVariant:
    # Private source syntax. Neither this value nor its tree is returned to callers.
    tree: ast.Module
    spelling: str
    functions: Tuple[Tuple[Tuple[int, int], Path], ...]


@dataclass(frozen=True)
class _OriginalSource:
    token: int
    variants: Tuple[_ParsedVariant, ...]
    retained_bytes: int


@dataclass(frozen=True)
class _OriginalLocal:
    facts: Optional[Tuple[Path, _LocalTransfer]]
    compatible: bool


_Location = Tuple[Tuple[IfSuiteStep, ...], SuiteField, int]
_LocalKey = Tuple[int, IfTransferSite, Tuple[str, ...], _Location]


class _SourceCache:
    """Only private parsed source and frozen facts, owned by one analyze_files context."""

    def __init__(self, entries: int, source_bytes: int, local_entries: int) -> None:
        self.maximum_source_bytes = source_bytes
        self.enabled = entries > 0 and source_bytes > 0 and local_entries > 0
        self.serial = 0
        self.sources: BoundedCache[Tuple[str, ParserConfiguration], _OriginalSource] = BoundedCache(
            entries, weight=lambda item: item.retained_bytes, weight_limit=source_bytes
        )
        self.locals: BoundedCache[_LocalKey, _OriginalLocal] = BoundedCache(local_entries)

    def known(self, source: str) -> Optional[_OriginalSource]:
        return self.sources.get((source, parser_configuration()))

    def original(self, source: str) -> Optional[_OriginalSource]:
        key = (source, parser_configuration())
        known = self.sources.get(key)
        if known is not None:
            return known
        variants: list[_ParsedVariant] = []
        for comments in (False, True):
            try:
                tree = ast.parse(source, type_comments=comments)
                spelling = exact_dump(tree)
            except (SyntaxError, ValueError, RecursionError):
                # False failing never reaches True in the baseline proof.
                if not comments:
                    break
                continue
            if any(variant.spelling == spelling for variant in variants):
                continue
            functions = tuple(
                ((node.lineno, node.col_offset), path)
                for node, path in path_map(tree.body).items()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            variants.append(_ParsedVariant(tree, spelling, functions))
        self.serial += 1
        size = len(source.encode("utf-8", errors="surrogatepass")) + sum(
            len(variant.spelling.encode("utf-8", errors="surrogatepass")) for variant in variants
        )
        original = _OriginalSource(self.serial, tuple(variants), size)
        # The existing cache keeps a single oversized value; this analysis must not.
        if size > self.maximum_source_bytes:
            return None
        self.sources[key] = original
        return original

    def local(
        self,
        original: _OriginalSource,
        source: str,
        site: IfTransferSite,
        outputs: Tuple[str, ...],
        location: _Location,
    ) -> _OriginalLocal:
        key = (original.token, site, outputs, location)
        if key in self.locals:
            return self.locals[key]
        answers: list[Optional[Tuple[Path, _LocalTransfer]]] = []
        for variant in original.variants:
            paths = [
                path for position, path in variant.functions if position == site.function_position
            ]
            answer: Optional[Tuple[Path, _LocalTransfer]] = None
            if len(paths) == 1:
                function = resolve_path(variant.tree.body, paths[0])
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nodes = _selected_suite(function, location, site.block_position[2])
                    if (
                        nodes is not None
                        and nodes
                        and (nodes[0].lineno, nodes[0].col_offset, len(nodes))
                        == site.block_position
                    ):
                        facts = _local_transfer(source, function, nodes, site, outputs)
                        if facts is not None:
                            answer = (paths[0], facts)
            answers.append(answer)
        result = _OriginalLocal(
            answers[0] if answers else None,
            bool(answers) and all(answer == answers[0] for answer in answers),
        )
        self.locals[key] = result
        return result


_SOURCE_CACHE: ContextVar[Optional[_SourceCache]] = ContextVar("if_transfer_sources", default=None)


@contextmanager
def sharing_if_transfer_sources(
    *, entries: int = 64, source_bytes: int = 8 * 1024 * 1024, local_entries: int = 2048
) -> Iterator[None]:
    """Bound private source facts to this analysis; nested scopes restore their parent."""
    if min(entries, source_bytes, local_entries) < 0:
        raise ValueError("Source certificate cache limits must be nonnegative")
    cache = _SourceCache(entries, source_bytes, local_entries)
    token = _SOURCE_CACHE.set(cache)
    try:
        yield
    finally:
        _SOURCE_CACHE.reset(token)
        cache.sources.clear()
        cache.locals.clear()


def _cache_for(source: str) -> Optional[_SourceCache]:
    cache = _SOURCE_CACHE.get()
    return (
        cache
        if (
            cache is not None
            and cache.enabled
            and memoizing()
            and len(source.encode("utf-8", errors="surrogatepass")) <= cache.maximum_source_bytes
            and parsing_is_pure(source)
        )
        else None
    )


def _selected_suite(
    function: FunctionNode, location: _Location, count: int
) -> Optional[Sequence[ast.stmt]]:
    path, field, start = location
    owner: Union[FunctionNode, ast.If] = function
    try:
        for step_field, index in path:
            suite = (
                owner.body
                if step_field == "body"
                else owner.orelse if isinstance(owner, ast.If) else ()
            )
            statement = suite[index]
            if not isinstance(statement, ast.If):
                return None
            owner = statement
        if not isinstance(owner, ast.If):
            return None
        suite = owner.body if field == "body" else owner.orelse
        selected = suite[start : start + count]
        return selected if len(selected) == count else None
    except (AttributeError, IndexError, TypeError):
        return None


def _local_transfer(
    source: str,
    function: FunctionNode,
    nodes: Sequence[ast.stmt],
    site: IfTransferSite,
    returned_names: Tuple[str, ...],
) -> Optional[_LocalTransfer]:
    """Local facts only refuse early; every admission still reproves exact original source."""
    if not nodes:
        return None
    try:
        location = _suite_location(function.body, nodes)
        if location is None:
            return None
        selected = _selected_node_ids(nodes)
        if selected is None:
            return None
        if not _unique_tree(function):
            return None
        if (
            site.module_digest != source_digest_of(source)
            or site.function_position != (function.lineno, function.col_offset)
            or site.block_position != (nodes[0].lineno, nodes[0].col_offset, len(nodes))
        ):
            return None
        names = frozenset(binding.name for binding in own_scope_bindings(nodes))
        if not names or names & frozenset(parameter_names(function.args)):
            return None
        if any(
            binding.name in names and binding.node_id not in selected
            for binding in own_scope_bindings(function.body)
        ):
            return None
        start_position = (nodes[0].lineno, nodes[0].col_offset)
        for node in ast.walk(function):
            if node is not function and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                if any(
                    isinstance(child, ast.Name) and child.id in names for child in ast.walk(node)
                ):
                    return None
            if isinstance(node, (ast.Global, ast.Nonlocal)) and names & set(node.names):
                return None
            if isinstance(node, ast.Name) and node.id in names:
                if (
                    isinstance(node.ctx, ast.Del)
                    or id(node) not in selected
                    and isinstance(node.ctx, ast.Load)
                    and (node.lineno, node.col_offset) < start_position
                ):
                    return None
        exit_bound = definitely_bound_after(nodes)
        if (
            exit_bound is None
            or not names <= exit_bound
            or not set(returned_names) <= names
            or len(set(returned_names)) != len(returned_names)
        ):
            return None
        if _fresh_flow(nodes, names) is None:
            return None
    except (AttributeError, TypeError, ValueError, RecursionError):
        return None
    return _LocalTransfer(location, names, frozenset(exit_bound))


def _certificate(
    local: _LocalTransfer,
    function_path: Path,
    site: IfTransferSite,
    count: int,
    outputs: Tuple[str, ...],
) -> IfBindingTransfer:
    path, field, index = local.location
    return IfBindingTransfer(
        site,
        function_path,
        path,
        field,
        index,
        count,
        local.initialized_names,
        local.normal_exit_bound,
        outputs,
    )


def _certify_uncached(
    source: str,
    module: ast.Module,
    function: FunctionNode,
    nodes: Sequence[ast.stmt],
    site: IfTransferSite,
    returned_names: Tuple[str, ...],
) -> Optional[IfBindingTransfer]:
    local = _local_transfer(source, function, nodes, site, returned_names)
    if local is None or not _source_corresponds(module, source):
        return None
    function_path = path_map(module.body).get(function)
    return (
        None
        if function_path is None
        else _certificate(local, function_path, site, len(nodes), returned_names)
    )


def certify_if_binding_transfer(
    source: str,
    module: ast.Module,
    function: FunctionNode,
    nodes: Sequence[ast.stmt],
    site: IfTransferSite,
    returned_names: Tuple[str, ...],
) -> Optional[IfBindingTransfer]:
    """Caller trees are proved afresh; owned analysis trees may share only source correspondence."""
    cache = _cache_for(source)
    if cache is None:
        return _certify_uncached(source, module, function, nodes, site, returned_names)
    if not nodes:
        return None
    try:
        location = _suite_location(function.body, nodes)
        if location is None or _selected_node_ids(nodes) is None:
            return None
        if (
            site.function_position != (function.lineno, function.col_offset)
            or (nodes[0].lineno, nodes[0].col_offset, len(nodes)) != site.block_position
        ):
            return None
        # A cold source must retain the existing zero-whole-module local refusal budget.
        # This current-tree refusal is never placed in the original-source memo.
        arguments: object = getattr(function, "args", None)
        if not isinstance(arguments, ast.arguments):
            return None
        tuple(parameter_names(arguments))
        original = cache.known(source)
        if original is None:
            if _local_transfer(source, function, nodes, site, returned_names) is None:
                return None
            original = cache.original(source)
        if original is None:
            token = _SOURCE_CACHE.set(None)
            try:
                return _certify_uncached(source, module, function, nodes, site, returned_names)
            finally:
                _SOURCE_CACHE.reset(token)
        local = cache.local(original, source, site, returned_names, location)
    except (AttributeError, TypeError, ValueError, RecursionError):
        return None
    if not local.compatible:
        return _certify_uncached(source, module, function, nodes, site, returned_names)
    if local.facts is None:
        return None
    function_path, facts = local.facts
    if not _source_corresponds(module, source):
        return None
    try:
        if resolve_path(module.body, function_path) is not function:
            return None
        selected = _selected_suite(function, facts.location, len(nodes))
        if selected is None or any(a is not b for a, b in zip(selected, nodes)):
            return None
    except (AttributeError, IndexError, TypeError):
        return None
    return _certificate(facts, function_path, site, len(nodes), returned_names)


def verify_if_binding_transfer(
    certificate: IfBindingTransfer, source: str, module: ast.Module
) -> bool:
    """Reprove stored facts on the current source-owned tree, including after reparse."""
    try:
        function = resolve_path(module.body, certificate.function_path)
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        owner: Union[FunctionNode, ast.If] = function
        for field, index in certificate.suite_path:
            if index < 0 or field not in ("body", "orelse"):
                return False
            if field == "body":
                statements = owner.body
            elif isinstance(owner, ast.If):
                statements = owner.orelse
            else:
                return False
            next_owner = statements[index]
            if not isinstance(next_owner, ast.If):
                return False
            owner = next_owner
        if not isinstance(owner, ast.If) or certificate.suite_field not in ("body", "orelse"):
            return False
        suite = owner.body if certificate.suite_field == "body" else owner.orelse
        nodes = suite[certificate.start : certificate.start + certificate.count]
    except (AttributeError, IndexError, TypeError):
        return False
    token = _CORRESPONDENCE_PROOFS.set(None)
    try:
        rebuilt = certify_if_binding_transfer(
            source, module, function, nodes, certificate.site, certificate.returned_names
        )
    finally:
        _CORRESPONDENCE_PROOFS.reset(token)
    return rebuilt == certificate
