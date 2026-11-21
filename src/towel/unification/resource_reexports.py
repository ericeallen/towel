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

import builtins
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, FrozenSet, Iterator, Optional

from .bounded_cache import BoundedCache
from .import_graph import ImportGraphCache, ImportLocation, import_location
from .program_imports import ProgramImports
from .resource_layout import OriginalLayout
from .resource_terminals import Namespace, TerminalIndex

_MAX_HOPS = 32


def _private_path(path: Path, stage: Path) -> bool:
    try:
        return path.is_relative_to(stage) and path.resolve() == path
    except (OSError, RuntimeError):
        return False


@dataclass(frozen=True, eq=False, slots=True)
class _Suffix:
    """An interned tail of a source spelling. The caller holds its own continuation."""

    name: str
    rest: Optional[_Suffix]
    # The full remaining attributes, only when they can complete a constructor.
    # This preserves the release's rule that an exact literal match stops there,
    # even if a project module also binds that spelling to a different resource.
    literal_tail: Optional[str]


@dataclass(eq=False, slots=True)
class _State:
    receiver: Namespace
    suffix: _Suffix
    answers: dict[Namespace, int] = field(default_factory=dict)
    waiting: dict[tuple[_State, Optional[_Suffix]], int] = field(default_factory=dict)


class ReexportSearch:
    """Resolve a binding once and resume its callers with the modules or names it denotes.

    A state reads one source spelling's suffix from one receiver. Following an
    import starts a separate lookup. On return its caller consumes the rest
    of its own spelling, so a star cycle never manufactures longer suffixes.
    Each state's outgoing rules are expanded once. A result is propagated again
    only if it took fewer of the 32 allowed import hops.
    """

    def __init__(
        self, graph: ImportGraphCache, constructors: FrozenSet[str], stage: Optional[Path] = None
    ) -> None:
        self.graph = graph
        self.budget = graph.resource_budget
        self.layout = OriginalLayout(self.budget)
        self.known: BoundedCache[tuple[Path, FrozenSet[str]], FrozenSet[str]] = BoundedCache(2048)
        self.constructors = constructors
        self.stage = stage
        self.shareable = True
        self.prefixes = {
            ".".join(name.split(".")[:end])
            for name in constructors
            for end in range(1, name.count(".") + 2)
        }
        self.literal_tails = {
            name[dot:] for name in constructors for dot, char in enumerate(name) if char == "."
        }
        self.suffixes: dict[tuple[str, Optional[_Suffix], Optional[str]], _Suffix] = {}
        self.states: dict[tuple[Namespace, _Suffix], _State] = {}
        self.programs: dict[int, ProgramImports] = {}
        self.roots: dict[tuple[Path, str], tuple[Namespace, ...]] = {}
        self.children: dict[tuple[Namespace, str], Optional[Namespace]] = {}
        self.exports: dict[tuple[Path, str], FrozenSet[str]] = {}
        self.terminals = TerminalIndex(
            self.budget, graph.terminal_facts, constructors, stage, self._namespace, self._child
        )
        self.expand: deque[_State] = deque()
        self.deliver: deque[tuple[_State, Optional[_Suffix], Namespace, int]] = deque()

    def _suffix(self, parts: list[str], outer: Optional[str]) -> _Suffix:
        # A terminal node remembers only whether the outstanding caller suffix
        # could complete a literal constructor. All other caller tails agree.
        rest: Optional[_Suffix] = None
        for name in ("", *reversed(parts)):
            self.budget.take()
            literal = outer
            if rest is not None:
                literal = None if rest.literal_tail is None else "." + name + rest.literal_tail
                if literal not in self.literal_tails:
                    literal = None
            key = (name, rest, literal)
            known = self.suffixes.get(key)
            if known is None:
                known = self.suffixes[key] = _Suffix(*key)
            rest = known
        assert rest is not None
        return rest

    def _receiver(self, location: ImportLocation, parts: tuple[str, ...]) -> Optional[Namespace]:
        if location.path is None:
            return None
        path = location.path.joinpath(*parts)
        directory = path if path.is_dir() else None
        module = location.definition(parts)
        if self.stage is not None and self.shareable:
            # Absolute imports select a definition in the original layout
            # before reading its staged copy. A new module, initializer or
            # symlink in the original can change another importer's answer.
            program = location.program
            if (
                program is not None
                and program.stage_root == self.stage
                and path.is_relative_to(program.model.root)
            ):
                self.shareable = self.layout.observe(path.parent) and (
                    directory is None or self.layout.observe(directory)
                )
            else:
                # A dangling symlink can acquire a target without any write
                # in the private stage. Include both candidate definition files.
                self.shareable = (
                    _private_path(path, self.stage)
                    and not (path / "__init__.py").is_symlink()
                    and not (parts and path.with_suffix(".py").is_symlink())
                )
            if module is not None and not _private_path(module, self.stage):
                self.shareable = False
        if module is None and directory is None:
            return None
        program_id = 0
        if directory is not None and location.program is not None:
            program_id = id(location.program)
            self.programs[program_id] = location.program
        return Namespace(module, directory, program_id)

    def _roots(self, file: Path, head: str) -> tuple[Namespace, ...]:
        # Relative dots use only the importing directory. Absolute heads use
        # only its ProgramImports. import_location uses exactly these dependencies.
        key = (file.parent if head.startswith(".") else self.graph.program_root_for(file), head)
        known = self.roots.get(key)
        if known is None:
            location = import_location(file, head, self.graph)
            receiver = self._receiver(location, ()) if location is not None else None
            known = (() if receiver is None else (receiver,)) + (
                (Namespace(spelling=head),) if head in self.prefixes else ()
            )
            self.roots[key] = known
        return known

    def _namespace(self, file: Path, head: str) -> Optional[Namespace]:
        return next((root for root in self._roots(file, head) if not root.spelling), None)

    def _child(self, receiver: Namespace, name: str) -> Optional[Namespace]:
        key = (receiver, name)
        if key not in self.children:
            child = None
            if receiver.directory is not None:
                location = ImportLocation(receiver.directory, self.programs.get(receiver.program))
                child = self._receiver(location, (name,))
            elif receiver.spelling:
                spelling = receiver.spelling + "." + name
                if spelling in self.prefixes:
                    child = Namespace(spelling=spelling)
            self.children[key] = child
        return self.children[key]

    def _state(self, receiver: Namespace, suffix: _Suffix) -> _State:
        key = (receiver, suffix)
        known = self.states.get(key)
        if known is None:
            self.budget.take()
            known = self.states[key] = _State(receiver, suffix)
            self.expand.append(known)
        return known

    def _origins(self, file: Path, origin: str, outer: Optional[str]) -> Iterator[_State]:
        self.budget.take(1 + len(origin) // 64)
        absolute = origin.lstrip(".")
        parts = absolute.split(".")
        head = origin[: len(origin) - len(absolute)] or parts.pop(0)
        suffix = self._suffix(parts, outer)
        if outer is not None and origin + outer in self.constructors:
            yield self._state(Namespace(spelling=head), suffix)
        else:
            for receiver in self._roots(file, head):
                yield self._state(receiver, suffix)

    def _listen(self, source: _State, target: _State, rest: Optional[_Suffix], cost: int) -> None:
        self.budget.take()
        key = (target, rest)
        if cost >= source.waiting.get(key, _MAX_HOPS + 1):
            return
        source.waiting[key] = cost
        for value, hops in source.answers.items():
            self._queue(target, rest, value, cost + hops)

    def _queue(self, target: _State, rest: Optional[_Suffix], value: Namespace, hops: int) -> None:
        self.budget.take()
        if hops <= _MAX_HOPS:
            self.deliver.append((target, rest, value, hops))

    def _answer(self, target: _State, value: Namespace, hops: int) -> None:
        if hops >= target.answers.get(value, _MAX_HOPS + 1):
            return
        target.answers[value] = hops
        for (waiting, rest), cost in target.waiting.items():
            self._queue(waiting, rest, value, cost + hops)

    def _expand(self, state: _State, exported: Callable[[Path, str], FrozenSet[str]]) -> None:
        receiver, suffix = state.receiver, state.suffix
        rest = suffix.rest
        if rest is None:
            self._answer(state, receiver, 0)
            return
        child = self._child(receiver, suffix.name)
        if child is not None:
            self._listen(self._state(child, rest), state, None, 0)
        if receiver.module is not None:
            key = (receiver.module, suffix.name)
            replacements = self.exports.get(key)
            if replacements is None:
                replacements = self.exports[key] = exported(*key)
            for origin in replacements:
                for start in self._origins(receiver.module, origin, rest.literal_tail):
                    self._listen(start, state, rest, 1)

    def _possible_origins(self, file: Path, origins: FrozenSet[str]) -> FrozenSet[str]:
        # A bare builtin falling through to stars, like NetworkX's AttributeError,
        # needs no dotted-name search when no reachable module can rebind it to a
        # constructor. The same proof works for ordinary calls in a star package.
        starts: set[Namespace] = set()
        for origin in origins:
            self.budget.take(1 + len(origin) // 64)
            absolute = origin.lstrip(".")
            head = origin[: len(origin) - len(absolute)] or absolute.partition(".")[0]
            root = self._namespace(file, head)
            if root is not None:
                starts.add(root)
        # The index pays for itself on packages whose initializers can branch.
        # An absolute project package can contain many re-exporting subpackages.
        if (
            starts
            and (
                self.terminals.covered
                or any(origin.rpartition(".")[2] in vars(builtins) for origin in origins)
                or any(not origin.startswith(".") for origin in origins)
                and any(root.directory is not None for root in starts)
                or len(origins) > 1
            )
            and self.terminals.covers(starts)
        ):
            return frozenset(
                origin for origin in origins if origin.rpartition(".")[2] in self.terminals.possible
            )
        return origins

    def follow(
        self,
        file: Path,
        origins: FrozenSet[str],
        exported: Callable[[Path, str], FrozenSet[str]],
        *,
        lexical_exports: bool,
    ) -> FrozenSet[str]:
        if lexical_exports:
            origins = self._possible_origins(file, origins)
        roots = {state for origin in origins for state in self._origins(file, origin, "")}
        while self.expand or self.deliver:
            if self.expand:
                self._expand(self.expand.popleft(), exported)
            else:
                target, rest, value, hops = self.deliver.popleft()
                if rest is None or rest.rest is None:
                    self._answer(target, value, hops)
                else:
                    self._listen(self._state(value, rest), target, None, hops)
        found: set[str] = set()
        for root in roots:
            self.budget.take(1 + len(root.answers))
            found.update(
                value.spelling for value in root.answers if value.spelling in self.constructors
            )
        return frozenset(found)


def reachable_constructors(
    importer: str | Path,
    origins: FrozenSet[str],
    graph: ImportGraphCache,
    constructors: FrozenSet[str],
    exported: Callable[[Path, str], FrozenSet[str]],
    *,
    search: Optional[ReexportSearch] = None,
) -> FrozenSet[str]:
    """Raise WorkLimitReached if the run cannot finish the proof within its budget."""
    return (search or ReexportSearch(graph, constructors)).follow(
        importer if search is not None and isinstance(importer, Path) else Path(importer).resolve(),
        origins,
        exported,
        lexical_exports=search is not None,
    )
