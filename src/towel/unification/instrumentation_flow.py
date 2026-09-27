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

"""Forward value flow for supported body instrumentation.

The resolver supplies binding identities and call semantics. This module only
tracks values through Python evaluation, including killing reassigned aliases.
Unknown operations carry no evidence of instrumentation. Namespace inspection
alone is deliberately not a reason to refuse extraction.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Iterable, Mapping, Sequence


class ValueKind(Enum):
    CLASS = auto()
    NAMESPACE = auto()
    METHOD = auto()
    ITEMS = auto()
    METHODS = auto()
    SOURCE = auto()
    CODE = auto()
    AST = auto()
    TRANSFORMER = auto()


@dataclass(frozen=True)
class Symbol:
    name: str
    imported: bool = False


@dataclass(frozen=True)
class Value:
    kinds: frozenset[ValueKind] = frozenset()
    symbols: frozenset[Symbol] = frozenset()
    targets: frozenset[tuple[str, str]] = frozenset()

    def join(self, other: Value) -> Value:
        return Value(
            self.kinds | other.kinds, self.symbols | other.symbols, self.targets | other.targets
        )

    @classmethod
    def kind(cls, kind: ValueKind, source: Value | None = None) -> Value:
        return cls(frozenset({kind}), targets=source.targets if source else frozenset())


UNKNOWN = Value()
Call = Callable[[ast.Call, Value, tuple[Value, ...], Mapping[str, Value]], Value]


class Flow:
    """A bounded, forward abstract evaluation with immutable values.

    Calls in branches are considered possible. Loops join entry and one body
    traversal; this recognizer does not claim to solve arbitrary metaprograms.
    Nested definitions are not executed. Comprehension bindings are scoped.
    """

    def __init__(
        self,
        call: Call,
        initial: Mapping[str, Value],
        definition: Callable[
            [ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, tuple[Value, ...]], Value
        ],
        resolve: Callable[[Value], Value],
    ) -> None:
        self.call = call
        self.definition = definition
        self.resolve = resolve
        self.facts = dict(initial)
        self.returned = UNKNOWN
        self.evaluated: dict[int, Value] = {}

    def value(self, node: ast.expr) -> Value:
        result = self._value(node)
        self.evaluated[id(node)] = result
        return result

    def _value(self, node: ast.expr) -> Value:
        if isinstance(node, ast.Name):
            return self.resolve(
                self.facts.get(node.id, Value(symbols=frozenset({Symbol(node.id)})))
            )
        if isinstance(node, ast.Attribute):
            owner = self.value(node.value)
            kinds: set[ValueKind] = set()
            if ValueKind.CLASS in owner.kinds:
                kinds.add(ValueKind.NAMESPACE if node.attr == "__dict__" else ValueKind.METHOD)
            if ValueKind.METHOD in owner.kinds and node.attr == "__code__":
                kinds.add(ValueKind.CODE)
            return Value(
                frozenset(kinds),
                frozenset(
                    Symbol(f"{symbol.name}.{node.attr}", symbol.imported)
                    for symbol in owner.symbols
                ),
                owner.targets,
            )
        if isinstance(node, ast.Subscript):
            owner = self.value(node.value)
            self.value(node.slice)
            kinds = set(owner.kinds & {ValueKind.SOURCE, ValueKind.CODE})
            if ValueKind.NAMESPACE in owner.kinds:
                kinds |= {ValueKind.METHOD}
            return Value(frozenset(kinds), targets=owner.targets)
        if isinstance(node, ast.Call):
            callee = self.value(node.func)
            arguments = tuple(self.value(arg) for arg in node.args)
            keywords = {kw.arg: self.value(kw.value) for kw in node.keywords if kw.arg is not None}
            for kw in node.keywords:
                if kw.arg is None:
                    self.value(kw.value)
            return self.call(node, callee, arguments, keywords)
        if isinstance(node, ast.NamedExpr):
            value = self.value(node.value)
            self.bind(node.target, value)
            return value
        if isinstance(node, ast.IfExp):
            self.value(node.test)
            return self.value(node.body).join(self.value(node.orelse))
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            saved = self.facts.copy()
            for generator in node.generators:
                self.iteration(generator.target, self.value(generator.iter))
                for condition in generator.ifs:
                    self.value(condition)
            if isinstance(node, ast.DictComp):
                self.value(node.key)
                self.value(node.value)
            else:
                self.value(node.elt)
            self.facts = saved
            return UNKNOWN
        if isinstance(node, ast.Lambda):
            return UNKNOWN
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self.value(child)
        return UNKNOWN

    def bind(self, target: ast.expr, value: Value) -> None:
        if isinstance(target, ast.Name):
            self.facts[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self.bind(element, UNKNOWN)
        elif isinstance(target, ast.Starred):
            self.bind(target.value, UNKNOWN)

    def iteration(self, target: ast.expr, value: Value) -> None:
        self.bind(target, UNKNOWN)
        if ValueKind.METHODS in value.kinds:
            self.bind(target, Value.kind(ValueKind.METHOD, value))
        if (
            ValueKind.ITEMS in value.kinds
            and isinstance(target, (ast.Tuple, ast.List))
            and len(target.elts) == 2
        ):
            self.bind(target.elts[1], Value.kind(ValueKind.METHOD, value))

    def branches(self, branches: Sequence[Sequence[ast.stmt]]) -> None:
        entry = self.facts.copy()
        exits: list[dict[str, Value]] = []
        for branch in branches:
            self.facts = entry.copy()
            self.statements(branch)
            exits.append(self.facts)
        self.facts = {
            name: _joined(exit.get(name, entry.get(name, UNKNOWN)) for exit in exits)
            for name in set(entry).union(*(set(exit) for exit in exits))
        }

    def statements(self, statements: Sequence[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, ast.Assign):
                if isinstance(node.value, (ast.Tuple, ast.List)):
                    values = tuple(self.value(elt) for elt in node.value.elts)
                    for target in node.targets:
                        if isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == len(
                            values
                        ):
                            for element, part in zip(target.elts, values):
                                self.bind(element, part)
                        else:
                            self.bind(target, UNKNOWN)
                else:
                    value = self.value(node.value)
                    for target in node.targets:
                        self.bind(target, value)
            elif isinstance(node, ast.AnnAssign):
                if node.value is not None:
                    self.bind(node.target, self.value(node.value))
            elif isinstance(node, ast.AugAssign):
                self.value(node.value)
                self.bind(node.target, UNKNOWN)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name if alias.asname else alias.name.partition(".")[0]
                    self.facts[alias.asname or name] = Value(
                        symbols=frozenset({Symbol(name, True)})
                    )
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.facts[alias.asname or alias.name] = (
                        UNKNOWN
                        if node.level
                        else Value(symbols=frozenset({Symbol(f"{node.module}.{alias.name}", True)}))
                    )
            elif isinstance(node, ast.ClassDef):
                self.facts[node.name] = self.definition(
                    node, tuple(self.value(base) for base in node.bases)
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.facts[node.name] = self.definition(node, ())
            elif isinstance(node, ast.Return):
                if node.value:
                    self.returned = self.returned.join(self.value(node.value))
                break
            elif isinstance(node, ast.Raise):
                if node.exc:
                    self.value(node.exc)
                if node.cause:
                    self.value(node.cause)
                break
            elif isinstance(node, ast.If):
                self.value(node.test)
                self.branches((node.body, node.orelse))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                value = self.value(node.iter)
                entry = self.facts.copy()
                self.iteration(node.target, value)
                self.statements(node.body)
                self.facts = {
                    name: value.join(entry.get(name, UNKNOWN)) for name, value in self.facts.items()
                }
                self.statements(node.orelse)
            elif isinstance(node, ast.While):
                self.value(node.test)
                self.branches((node.body, ()))
                self.statements(node.orelse)
            elif isinstance(node, (ast.Try, ast.TryStar)):
                self.branches((node.body, *(handler.body for handler in node.handlers)))
                self.statements(node.orelse)
                self.statements(node.finalbody)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    value = self.value(item.context_expr)
                    if item.optional_vars:
                        self.bind(item.optional_vars, value)
                self.statements(node.body)
            elif isinstance(node, ast.Match):
                self.value(node.subject)
                for case in node.cases:
                    if case.guard:
                        self.value(case.guard)
                self.branches(tuple(case.body for case in node.cases))
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    self.bind(target, UNKNOWN)
            else:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.expr):
                        self.value(child)


def _joined(values: Iterable[Value]) -> Value:
    result = UNKNOWN
    for value in values:
        result = result.join(value)
    return result
