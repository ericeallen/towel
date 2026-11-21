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

"""The locations a caller can display for the pairs declined in one analysis."""

from __future__ import annotations

from array import array
from bisect import bisect_right
from itertools import repeat
import pickle  # nosec B403: only a private temporary file written in this process
import tempfile
from threading import Lock
import weakref
from typing import Callable, Iterator, Literal

from ..reason_sentences import cross_module_condition, explain
from .models import BlockLocation, CodeBlockPair, LeftAlone

LocationDetail = Literal["counts", "largest", "explain", "all"]
# "all" is the engine API: it exposes the counted reason even when two
# reasons have the same printed sentence.
LARGEST_SHOWN = 5
Rank = tuple[int, tuple[tuple[str, int], ...]]
ModuleCondition = tuple[str, str, str]


def location_rank(site: LeftAlone) -> Rank:
    return -site.lines, tuple((block.path, block.first_line) for block in site.blocks)


class PairLocations:
    """Collect only the requested detail, in settlement order for ties and module conditions."""

    def __init__(
        self,
        detail: LocationDetail,
        cross_module: bool = False,
        public: Callable[[str], str] | None = None,
    ) -> None:
        self._public = public
        self._paths: dict[str, str] = {}
        self.detail = detail
        self.cross_module = cross_module
        self.conditions: dict[ModuleCondition, None] = {}
        self._reason_conditions: dict[str, str | None] = {}
        self._printed_reasons: dict[str, str] = {}
        self._sentence_reasons: dict[str, str] = {}
        self._largest: list[tuple[Rank, LeftAlone]] = []
        self._blocks: dict[BlockLocation, BlockLocation] = {}
        self._sites: dict[LeftAlone, int] = {}
        # Full output can repeat the same printed line, even interleaved with
        # others of the same rank. Keep those occurrences in their exact order.
        self._order = array("Q")
        self._counts = array("Q")

    def __bool__(self) -> bool:
        return bool(self._largest or self._sites or self.conditions)

    def _path(self, path: str) -> str:
        if self._public is None:
            return path
        if path not in self._paths:
            self._paths[path] = self._public(path)
        return self._paths[path]

    def _printed_reason(self, reason: str) -> str:
        if reason not in self._printed_reasons:
            self._printed_reasons[reason] = self._sentence_reasons.setdefault(
                explain(reason).sentence, reason
            )
        return self._printed_reasons[reason]

    def add(self, pair: CodeBlockPair, reason: str) -> None:
        if self.detail == "counts":
            return
        first_path, second_path = self._path(pair.file_path), self._path(pair.file_path2)
        if self.cross_module and first_path != second_path:
            if reason not in self._reason_conditions:
                self._reason_conditions[reason] = cross_module_condition(reason)
            condition = self._reason_conditions[reason]
            if condition is not None:
                first, second = sorted((first_path, second_path))
                self.conditions[first, second, condition] = None
        first_start, first_end = pair.block1_range
        second_start, second_end = pair.block2_range
        if self.detail == "largest":
            lines = (first_end - first_start + 1) + (second_end - second_start + 1)
            rank: Rank = -lines, ((first_path, first_start), (second_path, second_start))
            if len(self._largest) == LARGEST_SHOWN and rank >= self._largest[-1][0]:
                return
            index = bisect_right(self._largest, rank, key=lambda entry: entry[0])
            site = LeftAlone(
                (
                    BlockLocation(first_path, first_start, first_end),
                    BlockLocation(second_path, second_start, second_end),
                ),
                self._printed_reason(reason),
            )
            site = next((held for _, held in self._largest if held == site), site)
            self._largest.insert(index, (rank, site))
            del self._largest[LARGEST_SHOWN:]
            return
        if self.detail == "explain":
            reason = self._printed_reason(reason)
        else:
            reason = self._printed_reasons.setdefault(reason, reason)
        left = BlockLocation(first_path, first_start, first_end)
        right = BlockLocation(second_path, second_start, second_end)
        site = LeftAlone(
            (self._blocks.setdefault(left, left), self._blocks.setdefault(right, right)), reason
        )
        index = self._sites.setdefault(site, len(self._sites))
        if self._order and self._order[-1] == index:
            self._counts[-1] += 1
        else:
            self._order.append(index)
            self._counts.append(1)

    def finish(self) -> None:
        """The stage that named these files can now be released."""
        self._public = None
        self._paths.clear()
        self._blocks.clear()
        self._reason_conditions.clear()
        self._printed_reasons.clear()
        self._sentence_reasons.clear()

    def locations(self) -> Iterator[LeftAlone]:
        """Expand occurrences only for a report that requests them."""
        if self.detail == "largest":
            for _, site in self._largest:
                yield site
        elif self._sites:
            sites = list(self._sites)
            for index, count in zip(self._order, self._counts):
                yield from repeat(sites[index], count)


class SavedPairLocations:
    """A completed report still observable while the engine collects an independent analysis."""

    def __init__(self, locations: PairLocations) -> None:
        self._read_lock = Lock()
        self._file = tempfile.TemporaryFile()
        weakref.finalize(self, self._file.close)
        pickle.dump(locations, self._file, protocol=pickle.HIGHEST_PROTOCOL)

    def read(self) -> PairLocations:
        with self._read_lock:
            self._file.seek(0)
            # Only our own descriptor can access the unlinked temporary file.
            locations = pickle.load(self._file)  # nosec B301
        assert isinstance(locations, PairLocations)
        return locations
