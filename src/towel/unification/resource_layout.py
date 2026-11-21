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

"""The source-tree directory entries on which a staged re-export answer depends."""

from __future__ import annotations

import os
from pathlib import Path

from .resource_budget import ResourceBudget

_Signature = frozenset[tuple[str, bool]]


class OriginalLayout:
    """Check the names and kinds that can change a module lookup in the original tree."""

    def __init__(self, budget: ResourceBudget) -> None:
        self._budget = budget
        self._directories: dict[Path, _Signature] = {}

    def _signature(self, directory: Path, expected: _Signature | None = None) -> _Signature | None:
        self._budget.take(layout=True)
        try:
            if directory.resolve(strict=True) != directory:
                return None
            result: set[tuple[str, bool]] = set()
            count = 0
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index % 32 == 0:
                        self._budget.take(layout=True)
                    name = entry.name
                    module_name = name[:-3] if name.lower().endswith(".py") else name
                    if not module_name.isidentifier():
                        continue
                    # A symlink can start resolving (or change its target)
                    # without the link's name or kind in this directory changing.
                    is_directory = entry.is_dir(follow_symlinks=False)
                    if not is_directory and not entry.is_file(follow_symlinks=False):
                        return None
                    item = (name, is_directory)
                    if expected is None:
                        # Retaining an entry spends graph space as well as scan work.
                        self._budget.take()
                        result.add(item)
                    elif item not in expected:
                        return None
                    count += 1
            if expected is None:
                return frozenset(result)
            # Entry names are unique. Check cardinality and membership
            # without materializing another copy of the directory.
            return expected if count == len(expected) else None
        except (OSError, RuntimeError, ValueError):
            # Inaccessible directory or a changed or looping symlink ancestor.
            return None

    def observe(self, directory: Path) -> bool:
        """Record this directory once while resolving the current lookup."""
        if directory not in self._directories:
            signature = self._signature(directory)
            if signature is None:
                return False
            self._directories[directory] = signature
        return True

    def __bool__(self) -> bool:
        return bool(self._directories)

    def unchanged(self) -> bool:
        """Recheck every recorded directory before sharing a previous lookup."""
        return all(
            self._signature(path, expected) is not None
            for path, expected in self._directories.items()
        )
