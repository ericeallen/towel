# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Position-independent facts about the read-only source statements of one run."""

from __future__ import annotations

import ast
from contextvars import ContextVar
from hashlib import sha256
from typing import Callable, Generic, TypeVar
from weakref import WeakKeyDictionary

from ..canonical_ast import exact_dump
from .ast_paths import Step
from .bounded_cache import BoundedCache, memoizing

_T = TypeVar("_T")


class _StatementMemo(Generic[_T]):
    """Reuse a live node's answer or recover it by structure after a reparse."""

    def __init__(self) -> None:
        self._live: WeakKeyDictionary[ast.AST, _T] = WeakKeyDictionary()
        self._content: BoundedCache[bytes, _T] = BoundedCache(16_384)

    def get(self, node: ast.AST, compute: Callable[[ast.AST], _T]) -> _T:
        if not memoizing():
            return compute(node)
        try:
            return self._live[node]
        except KeyError:
            pass
        except TypeError:
            return compute(node)  # A bare ast.AST cannot be weakly referenced.
        try:
            key = sha256(exact_dump(node, include_positions=False).encode("utf-8")).digest()
        except RecursionError:
            # Both walks accept trees deeper than recursive structural formatting.
            return compute(node)
        if key in self._content:
            value = self._content[key]
        else:
            value = self._content.put(key, compute(node))
        self._live[node] = value
        return value


class RunSyntaxFacts:
    def __init__(self) -> None:
        self.unsupported: _StatementMemo[str | None] = _StatementMemo()
        self.literal_paths: _StatementMemo[tuple[tuple[Step, ...], ...]] = _StatementMemo()


run_syntax: ContextVar[RunSyntaxFacts | None] = ContextVar("run_syntax", default=None)
