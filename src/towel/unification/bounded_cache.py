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
from typing import Callable, Iterator, MutableMapping, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class BoundedCache(MutableMapping[K, V]):
    """A mapping that keeps at most ``limit`` entries, dropping the least recently used.

    Reading or writing an entry makes it the most recent. It is a
    ``MutableMapping``, so callers that register entries for eviction by
    path can ``pop`` them like any other table. With ``weight`` and
    ``weight_limit`` the table also drops the least recently used entries
    while the weights of those it holds sum to more than the limit, for
    values whose size varies by orders of magnitude (a clustering scan holds
    one site per similar block in the file).
    """

    def __init__(
        self,
        limit: int,
        *,
        weight: Optional[Callable[[V], int]] = None,
        weight_limit: Optional[int] = None,
    ) -> None:
        self._limit = limit
        self._weight = weight
        self._weight_limit = weight_limit
        self._weights: "OrderedDict[K, int]" = OrderedDict()
        self._total_weight = 0
        self._table: "OrderedDict[K, V]" = OrderedDict()

    def __getitem__(self, key: K) -> V:
        value = self._table[key]
        self._table.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        if key in self._table:
            self._forget_weight(key)
        self._table[key] = value
        self._table.move_to_end(key)
        if self._weight is not None:
            self._weights[key] = self._weight(value)
            self._total_weight += self._weights[key]
        while len(self._table) > self._limit or (
            self._weight_limit is not None
            and self._total_weight > self._weight_limit
            and len(self._table) > 1
        ):
            oldest, _ = self._table.popitem(last=False)
            self._forget_weight(oldest)

    def __delitem__(self, key: K) -> None:
        del self._table[key]
        self._forget_weight(key)

    def _forget_weight(self, key: K) -> None:
        if self._weight is not None:
            self._total_weight -= self._weights.pop(key, 0)

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
