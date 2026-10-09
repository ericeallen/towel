# Copyright 2026 Eric Allen
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

"""Standard mapping views tolerate their own LRU reads without changing cache policy."""

from __future__ import annotations

import gc
from typing import Literal, Optional
import weakref

import pytest

from towel.unification.bounded_cache import BoundedCache, memoization_disabled


def _cache() -> BoundedCache[str, int]:
    cache: BoundedCache[str, int] = BoundedCache(3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    return cache


@pytest.mark.parametrize("view", ["items", "values", "dict"])
def test_inherited_standard_mapping_views_read_every_entry(
    view: Literal["items", "values", "dict"],
) -> None:
    cache = _cache()
    if view == "items":
        assert list(cache.items()) == [("a", 1), ("b", 2), ("c", 3)]
    elif view == "values":
        assert list(cache.values()) == [1, 2, 3]
    else:
        assert dict(cache) == {"a": 1, "b": 2, "c": 3}
    assert list(cache) == ["a", "b", "c"]


def test_key_iteration_supports_value_reads_and_nested_mapping_views() -> None:
    cache = _cache()
    rows = [(key, cache[key], tuple(cache.items())) for key in cache]
    assert [row[:2] for row in rows] == [("a", 1), ("b", 2), ("c", 3)]
    assert [row[2] for row in rows] == [
        (("b", 2), ("c", 3), ("a", 1)),
        (("c", 3), ("a", 1), ("b", 2)),
        (("a", 1), ("b", 2), ("c", 3)),
    ]


def test_partial_view_reads_still_promote_entries_before_eviction() -> None:
    cache = _cache()
    items = iter(cache.items())
    assert next(items) == ("a", 1)
    assert list(cache) == ["b", "c", "a"]
    cache["d"] = 4
    assert "b" not in cache
    assert list(cache) == ["c", "a", "d"]


def test_weighted_views_do_not_change_accounting_or_reuse_policy() -> None:
    cache: BoundedCache[str, int] = BoundedCache(3, weight=lambda value: value, weight_limit=6)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    assert tuple(cache.values()) == (1, 2, 3)
    assert cache._total_weight == 6
    assert dict(cache._weights) == {"a": 1, "b": 2, "c": 3}
    cache["d"] = 4
    assert list(cache.items()) == [("d", 4)]
    assert cache._total_weight == 4
    cache.clear()
    cache["e"] = 2
    assert list(cache.items()) == [("e", 2)]
    assert cache._total_weight == 2


def test_none_and_false_values_are_present_in_standard_views() -> None:
    cache: BoundedCache[str, Optional[bool]] = BoundedCache(3)
    cache["none"] = None
    cache["false"] = False
    assert dict(cache.items()) == {"none": None, "false": False}
    assert list(cache.values()) == [None, False]
    assert "none" in cache and "false" in cache


def test_disabled_reads_remain_unavailable_and_cleanup_still_releases_storage() -> None:
    cache = _cache()
    with memoization_disabled():
        assert list(cache) == ["a", "b", "c"]
        with pytest.raises(KeyError):
            next(iter(cache.values()))
        assert cache.get("a") is None
        cache.clear()
        assert not list(cache)
    assert list(cache.items()) == []


def test_snapshot_iterator_lifetime_is_explicit_and_ends_with_the_iterator() -> None:
    class Owner:
        pass

    owner = Owner()
    reference = weakref.ref(owner)
    cache: BoundedCache[Owner, int] = BoundedCache(1)
    cache[owner] = 1
    keys = iter(cache)
    del owner
    cache.clear()
    gc.collect()
    assert reference() is not None, "An explicitly retained snapshot iterator owns its keys"
    del keys
    gc.collect()
    assert reference() is None
