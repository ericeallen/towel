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

"""Follow direct base aliases to Protocol without treating protocol implementers as protocols.

Only Protocol itself as a direct base makes a class a protocol. A class
statement ends the search, even when that class derives from a protocol.
Project imports, reexports and assignment aliases are followed through all
branches; conditional expressions and subscripted bases keep their possible
origins. This is not an evaluation of arbitrary calls or external libraries.
"""

from __future__ import annotations

import ast
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Optional, Set, Tuple

from ..source_text import read_source
from .import_graph import (
    ImportGraphCache,
    _module_scope_bindings,
    imported_alias_sites,
    module_scope_statements,
)
from .module_bindings import (
    PROTOCOL_BASES,
    ModuleBindings,
    dotted_name,
    global_bindings,
    import_origin,
)


class _BaseValue(Enum):
    ORDINARY = "ordinary"
    PROTOCOL = "protocol"
    DYNAMIC = "dynamic"


def _joined(values: Tuple[_BaseValue, ...]) -> _BaseValue:
    if _BaseValue.PROTOCOL in values:
        return _BaseValue.PROTOCOL
    return _BaseValue.DYNAMIC if _BaseValue.DYNAMIC in values else _BaseValue.ORDINARY


class _ProtocolBases:
    """One query's immutable trees and visited alias edges; no verdict survives the query."""

    def __init__(
        self,
        path: str,
        tree: ast.Module,
        bindings: ModuleBindings,
        cache: ImportGraphCache,
        parse: Callable[[str], ast.Module],
    ) -> None:
        self.path = cache.resolve(path)
        self.cache = cache
        self.parse = parse
        self.trees: Dict[Path, Optional[ast.Module]] = {self.path: tree}
        self.bindings: Dict[Path, Optional[ModuleBindings]] = {self.path: bindings}
        self.seen: Set[Tuple[Path, str]] = set()

    def _tree(self, path: Path) -> Optional[ast.Module]:
        if path not in self.trees:
            try:
                source = read_source(path)
                self.trees[path] = self.parse(source)
                self.bindings[path] = global_bindings(source)
            except (OSError, UnicodeError, SyntaxError, ValueError):
                self.trees[path] = None
        return self.trees[path]

    def expression(self, node: ast.expr, path: Path, depth: int = 0) -> _BaseValue:
        if isinstance(node, (ast.Subscript, ast.Starred, ast.NamedExpr)):
            return self.expression(node.value, path, depth)
        if isinstance(node, ast.IfExp):
            if isinstance(node.test, ast.Constant):
                branch = node.body if node.test.value else node.orelse
                return self.expression(branch, path, depth)
            return _joined(
                (
                    self.expression(node.body, path, depth),
                    self.expression(node.orelse, path, depth),
                )
            )
        if isinstance(node, (ast.Tuple, ast.List)):
            return _joined(tuple(self.expression(item, path, depth) for item in node.elts))
        if isinstance(node, ast.BoolOp):
            return _joined(tuple(self.expression(item, path, depth) for item in node.values))
        name = dotted_name(node)
        return self.named(name, path, depth) if name is not None else _BaseValue.DYNAMIC

    def named(self, name: str, path: Path, depth: int) -> _BaseValue:
        # A growing cyclic alias (a = a.member) may never repeat its spelling.
        # Refuse that bounded alias search, not every unresolved external base.
        if depth > 64:
            return _BaseValue.DYNAMIC
        key = path, name
        if key in self.seen:
            return _BaseValue.ORDINARY
        self.seen.add(key)
        tree = self._tree(path)
        if tree is None:
            return _BaseValue.ORDINARY
        head, _, rest = name.partition(".")
        bindings = self.bindings.get(path)
        if bindings is not None and head in bindings.rebound_by_global:
            return _BaseValue.DYNAMIC
        values = []
        for statement, alias in _module_scope_bindings(tree, head):
            if alias is not None and isinstance(statement, (ast.Import, ast.ImportFrom)):
                values.append(self._import(statement, alias, rest, path, depth))
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                if value is not None:
                    if rest:
                        target = dotted_name(value)
                        values.append(
                            self.named(f"{target}.{rest}", path, depth + 1)
                            if target is not None
                            else _BaseValue.DYNAMIC
                        )
                    else:
                        values.append(self.expression(value, path, depth + 1))
            elif isinstance(statement, ast.ClassDef) and not rest:
                # A plain ClassDef names that class, not its ancestors. Its
                # explicit decorator can instead replace it with Protocol.
                bindings = self.bindings.get(path)
                if statement.decorator_list and (
                    bindings is None or not bindings.keeps_namespace(statement.name)
                ):
                    values.append(_BaseValue.DYNAMIC)
            else:
                # A class namespace member, loop target, walrus, pattern or
                # other binding can hold Protocol. Ignoring a binding does
                # not establish the ordinary-base case.
                values.append(_BaseValue.DYNAMIC)
        for statement in module_scope_statements(tree):
            if isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    if alias.name == "*":
                        values.append(self._import(statement, alias, name, path, depth))
        return _joined(tuple(values))

    def _import(
        self,
        statement: ast.Import | ast.ImportFrom,
        alias: ast.alias,
        rest: str,
        path: Path,
        depth: int,
    ) -> _BaseValue:
        origin = import_origin(statement, alias)
        if alias.name == "*" and isinstance(statement, ast.ImportFrom):
            origin = statement.module if statement.level == 0 else None
        if origin is not None and (f"{origin}.{rest}" if rest else origin) in PROTOCOL_BASES:
            return _BaseValue.PROTOCOL
        sites = imported_alias_sites(str(path), statement, alias, rest, self.cache)
        return _joined(tuple(self.named(name, module, depth + 1) for module, name in sites or ()))


def bases_allow_private_helpers(
    klass: ast.ClassDef,
    tree: ast.Module,
    bindings: ModuleBindings,
    path: str,
    cache: ImportGraphCache,
    parse: Callable[[str], ast.Module],
) -> bool:
    """Whether no direct base requires a protocol contract or an unevaluated base expression.

    Ordinary named library bases need no allowlist. A computed base whose
    value is not statically resolved takes a module helper instead: its
    value could be Protocol, which would gain a structural requirement.
    """
    if not klass.bases:
        return True
    resolver = _ProtocolBases(path, tree, bindings, cache, parse)
    return all(
        resolver.expression(base, resolver.path) is _BaseValue.ORDINARY for base in klass.bases
    )
