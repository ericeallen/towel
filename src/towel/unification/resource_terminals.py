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

"""Which final attributes a project re-export could rename to a known constructor."""

from __future__ import annotations

import ast
import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, FrozenSet, Iterable, Optional

from ..analysis_sources import (
    ParserConfiguration,
    parse_analysis_source,
    parser_configuration,
    parsing_is_pure,
)
from ..source_text import try_read_source
from .bounded_cache import BoundedCache
from .module_bindings import import_origin
from .resource_budget import ResourceBudget
from .resource_terminal_source import terminal_syntax
from .statement_facts import imported_binding_name
from .visitors import evaluated_before_definition


@dataclass(frozen=True)
class Namespace:
    module: Optional[Path] = None
    directory: Optional[Path] = None
    program: int = 0
    spelling: str = ""


class IncompleteNamespace(Exception):
    """The available files cannot certify the set of possible bindings."""


@dataclass(frozen=True)
class TerminalFacts:
    """What this exact source can import or rename, independent of its location."""

    heads: FrozenSet[str]
    renames: FrozenSet[tuple[str, str]]


@dataclass
class TerminalIndex:
    """A closed overestimate of the modules any rewrite from the given roots could inspect.

    A rewrite with unconsumed attributes preserves the last one. A star
    import preserves it too. Only an explicit import or a dotted assignment
    can rename the last attribute. Collect all such renamings, ignoring
    their order and scope within each module. If even this larger relation
    cannot lead to a constructor, the full search would find none.
    """

    budget: ResourceBudget
    facts: BoundedCache[tuple[ParserConfiguration, bytes], TerminalFacts]
    constructors: FrozenSet[str]
    stage: Optional[Path]
    roots: Callable[[Path, str], Optional[Namespace]]
    child: Callable[[Namespace, str], Optional[Namespace]]
    covered: set[Namespace] = field(default_factory=set)
    modules: set[Path] = field(default_factory=set)
    renames: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    possible: FrozenSet[str] = frozenset()
    complete: bool = True

    def _source_facts(self, source: str, file: Path) -> TerminalFacts:
        if parsing_is_pure(source):
            try:
                module = parse_analysis_source(source, str(file))
            except SyntaxError:
                return TerminalFacts(frozenset(), frozenset())
        else:
            projected = terminal_syntax(source, self.budget)
            if projected is None:
                raise IncompleteNamespace
            module = projected
        renames: set[tuple[str, str]] = set()
        heads = {"builtins"}  # deferred open also names builtins.open
        pending: list[ast.AST] = list(module.body)
        while pending:
            node = pending.pop()
            self.budget.take()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                pending.extend(evaluated_before_definition(node))
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    self.budget.take()
                    if isinstance(node, ast.ImportFrom):
                        heads.add("." * node.level or (node.module or "").partition(".")[0])
                        last = alias.name
                    else:
                        heads.add(alias.name.partition(".")[0])
                        last = (import_origin(node, alias) or "").rpartition(".")[2]
                    name = imported_binding_name(alias)
                    if name and last != name:
                        renames.add((last, name))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                if isinstance(value, (ast.Attribute, ast.Name)):
                    last = value.attr if isinstance(value, ast.Attribute) else value.id
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and last != target.id:
                            renames.add((last, target.id))
            pending.extend(ast.iter_child_nodes(node))
        return TerminalFacts(frozenset(heads), frozenset(renames))

    def _bindings(self, file: Path) -> FrozenSet[str]:
        if self.stage is not None and not file.is_relative_to(self.stage):
            raise IncompleteNamespace
        # Re-read every provider. A fixed-point pass can rewrite a source
        # without changing its size or mtime. Only its syntax is shareable:
        # the importing directory and the module layout are checked afresh.
        self.budget.take(1 + file.stat().st_size // 4096)
        source = try_read_source(file)
        if source is None:
            raise IncompleteNamespace
        key = (parser_configuration(), hashlib.sha256(source.encode("utf-8")).digest())
        facts = self.facts.get(key)
        if facts is None:
            facts = self.facts.put(key, self._source_facts(source, file))
        self.budget.take(len(facts.renames))
        for last, name in facts.renames:
            self.renames[last].add(name)
        return facts.heads

    def covers(self, starts: Iterable[Namespace]) -> bool:
        if not self.complete:
            return False
        pending = list(starts)
        old_count = len(self.covered)
        try:
            while pending:
                self.budget.take()
                space = pending.pop()
                if space in self.covered:
                    continue
                self.covered.add(space)
                if space.module is not None and space.module not in self.modules:
                    self.modules.add(space.module)
                    for head in self._bindings(space.module):
                        linked = self.roots(space.module, head)
                        if linked is not None:
                            pending.append(linked)
                if space.directory is not None:
                    if space.directory.is_symlink():
                        raise IncompleteNamespace
                    with os.scandir(space.directory) as entries:
                        for entry in entries:
                            self.budget.take()
                            name = entry.name
                            if name.lower().endswith(".py"):
                                name = name[:-3]
                            if name.isidentifier():
                                child = self.child(space, name)
                                if child is not None:
                                    pending.append(child)
        except (OSError, ValueError, RecursionError, IncompleteNamespace):
            self.complete = False
            return False
        if not self.possible or len(self.covered) != old_count:
            names = {target.rpartition(".")[2] for target in self.constructors}
            todo = list(names)
            while todo:
                for name in self.renames.get(todo.pop(), ()):
                    self.budget.take()
                    if name not in names:
                        names.add(name)
                        todo.append(name)
            self.possible = frozenset(names)
        return True
