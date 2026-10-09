# Copyright 2025-2026 Eric Allen
# Licensed under the Apache License, Version 2.0.
"""Cleanup owns storage independently of the memoization read/write policy."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import gc
import weakref

import pytest

from towel.unification.bounded_cache import BoundedCache, memoization_disabled


@pytest.mark.parametrize("disabled", [False, True])
def test_clear_releases_entries_and_resets_weight_bookkeeping(disabled: bool) -> None:
    cache: BoundedCache[int, tuple[int, ...]] = BoundedCache(3, weight=len, weight_limit=3)
    cache[1] = (1, 2)
    cache[2] = (3,)
    with memoization_disabled() if disabled else nullcontext():
        cache.clear()
        assert len(cache) == 0
    cache[3] = (4, 5)
    cache[4] = (6,)
    assert list(cache) == [3, 4]
    assert cache[3] == (4, 5) and cache[4] == (6,)
    cache[5] = (7,)
    assert list(cache) == [4, 5]


@dataclass(frozen=True)
class _Owner:
    identity: int


@pytest.mark.parametrize("disabled", [False, True])
def test_clear_releases_strong_owner_keys(disabled: bool) -> None:
    cache: BoundedCache[_Owner, int] = BoundedCache(2)

    def keep_owner() -> weakref.ReferenceType[_Owner]:
        owner = _Owner(1)
        cache[owner] = 2
        return weakref.ref(owner)

    reference = keep_owner()
    gc.collect()
    assert reference() is not None
    with memoization_disabled() if disabled else nullcontext():
        cache.clear()
    gc.collect()
    assert reference() is None
