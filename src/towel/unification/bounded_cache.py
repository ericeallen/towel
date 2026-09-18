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

"""A small least-recently-used table; the newest entries survive."""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterator, MutableMapping, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class BoundedCache(MutableMapping[K, V]):
    """A mapping that keeps at most ``limit`` entries, dropping the least recently used.

    Reading or writing an entry makes it the most recent. It is a
    ``MutableMapping``, so callers that register entries for eviction by
    path can ``pop`` them like any other table.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._table: "OrderedDict[K, V]" = OrderedDict()

    def __getitem__(self, key: K) -> V:
        value = self._table[key]
        self._table.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        self._table[key] = value
        self._table.move_to_end(key)
        while len(self._table) > self._limit:
            self._table.popitem(last=False)

    def __delitem__(self, key: K) -> None:
        del self._table[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._table)

    def __len__(self) -> int:
        return len(self._table)

    def __contains__(self, key: object) -> bool:
        return key in self._table

    def put(self, key: K, value: V) -> V:
        """Store ``value`` under ``key`` and return it, for use in an expression."""
        self[key] = value
        return value
