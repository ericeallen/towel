# Copyright 2025-2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Immutable read facts for an engine's original, read-only source functions."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, FrozenSet, Iterator, Optional, Set, Tuple

from .bounded_cache import BoundedCache, memoizing
from .models import FunctionNode
from .semantic_safety import walk_own_scope


@dataclass(frozen=True)
class StatementUsage:
    lineno: int
    kind: str
    names: FrozenSet[str]


class _SourceUses:
    def __init__(self, owner: object, entries: int) -> None:
        self.owner = owner
        self.enabled = entries > 0
        self.rows: BoundedCache[FunctionNode, Tuple[StatementUsage, ...]] = BoundedCache(entries)


_SOURCE_USES: ContextVar[Optional[_SourceUses]] = ContextVar("return_source_uses", default=None)


@contextmanager
def sharing_return_source_uses(owner: object, *, entries: int = 2048) -> Iterator[None]:
    """Internal scope: the engine keeps original function trees read-only throughout."""
    if entries < 0:
        raise ValueError("Source usage cache limit must be nonnegative")
    uses = _SourceUses(owner, entries)
    token = _SOURCE_USES.set(uses)
    try:
        yield
    finally:
        _SOURCE_USES.reset(token)
        uses.rows.clear()


def _statement_usages(
    function: FunctionNode,
    read_names: Callable[[ast.AST], Set[str]],
    after_line: Optional[int],
) -> Iterator[StatementUsage]:
    for statement in function.body:
        for node in walk_own_scope(statement):
            if not isinstance(node, ast.stmt) or not hasattr(node, "lineno"):
                continue
            if after_line is not None and node.lineno <= after_line:
                continue
            yield StatementUsage(node.lineno, node.__class__.__name__, frozenset(read_names(node)))


def return_statement_usages(
    owner: object,
    function: FunctionNode,
    read_names: Callable[[ast.AST], Set[str]],
    after_line: int,
) -> Iterator[StatementUsage]:
    """No return-set memo: fresh callers filter immutable source rows each time."""
    scope = _SOURCE_USES.get()
    if scope is None or not scope.enabled or scope.owner is not owner or not memoizing():
        yield from _statement_usages(function, read_names, after_line)
        return
    rows = scope.rows.get(function)
    if rows is None:
        rows = tuple(_statement_usages(function, read_names, None))
        scope.rows[function] = rows
    yield from (row for row in rows if row.lineno > after_line)
