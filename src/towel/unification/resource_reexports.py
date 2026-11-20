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

"""Bounded reachability of recognized constructors through project re-exports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, FrozenSet, Iterator, Mapping, Optional

from .import_graph import ImportGraphCache, import_location


@dataclass(frozen=True, eq=False)
class _Suffix:
    """Alternative next attributes. Several spellings can share the same remaining suffix."""

    branches: Mapping[str, FrozenSet[_Suffix]]


_MAX_HOPS = 32
_END = _Suffix({})
_Tails = FrozenSet[_Suffix]


def _branches(tails: _Tails) -> dict[str, set[_Suffix]]:
    branches: dict[str, set[_Suffix]] = defaultdict(set)
    for tail in tails:
        for name, rest in tail.branches.items():
            branches[name].update(rest)
    return branches


def _without(tails: _Tails, suffix: tuple[str, ...]) -> _Tails:
    """Remove one terminal spelling, sharing every other suffix."""
    if not suffix:
        return tails - {_END}
    branches = _branches(tails)
    head, *rest = suffix
    if head not in branches:
        return tails
    old = frozenset(branches[head])
    new = _without(old, tuple(rest))
    if new == old:
        return tails
    branches[head] = set(new)
    kept = {name: frozenset(children) for name, children in branches.items() if children}
    return (tails & {_END}) | (frozenset({_Suffix(kept)}) if kept else frozenset())


def reachable_constructors(
    importer: str,
    origins: FrozenSet[str],
    graph: ImportGraphCache,
    constructors: FrozenSet[str],
    exported: Callable[[Path, str], FrozenSet[str]],
) -> FrozenSet[str]:
    """Follow at most 32 import bindings, counting each breadth-first layer as one hop.

    Removing a cycle from a path to a constructor only shortens it. No answer depends
    on a branch's visited set. A cycle through several star imports can nevertheless
    prepend a different attribute on every hop. Keep the possible suffixes together:
    resolving a binding replaces its first name and carries its entire suffix forward.
    """
    file = Path(importer).resolve()
    pending = {(file, origin): {_END} for origin in origins}
    found: set[str] = set()
    # These tables belong to this one traversal, including all its branches.
    targets = lru_cache(maxsize=None)(exported)
    roots = lru_cache(maxsize=None)(import_location)

    @lru_cache(maxsize=None)
    def module_at(file: Path, head: str, parts: tuple[str, ...]) -> tuple[Optional[Path], bool]:
        location = roots(file, head, graph)
        if location is None or location.path is None:
            return None, False
        return location.definition(parts), location.path.joinpath(*parts).is_dir()

    def binding_sites(file: Path, head: str, tails: _Tails) -> Iterator[tuple[Path, str, _Tails]]:
        """Each split into a project module, a binding there, and its remaining attributes."""
        prefixes: list[tuple[tuple[str, ...], _Tails]] = [((), tails)]
        while prefixes:
            parts, remaining = prefixes.pop()
            branches = _branches(remaining)
            if not branches:
                continue
            module, directory = module_at(file, head, parts)
            for name, children in branches.items():
                rest = frozenset(children)
                if module is not None:
                    yield module, name, rest
                if directory:
                    prefixes.append(((*parts, name), rest))

    for depth in range(_MAX_HOPS + 1):
        following: dict[tuple[Path, str], set[_Suffix]] = defaultdict(set)
        for (file, origin), alternatives in pending.items():
            tails = frozenset(alternatives)
            for target in constructors:
                if target == origin or target.startswith(origin + "."):
                    rest = tuple(target[len(origin) + 1 :].split(".")) if target != origin else ()
                    kept = _without(tails, rest)
                    if kept != tails:
                        found.add(target)
                        tails = kept
            if not tails or depth == _MAX_HOPS:
                continue
            absolute = origin.lstrip(".")
            dots = origin[: len(origin) - len(absolute)]
            parts = absolute.split(".")
            head = dots or parts.pop(0)
            for name in reversed(parts):
                tails = frozenset({_Suffix({name: tails})})
            for module, binding, suffixes in binding_sites(file, head, tails):
                for replacement in targets(module, binding):
                    following[module, replacement].update(suffixes)
        if not following:
            break
        pending = following
    return frozenset(found)
