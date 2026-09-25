"""Towel's notion of which names a function binds and reads, checked against CPython's.

A seeded generator writes small functions out of every construct Python has
that binds a name (assignment of every shape, augmented and annotated
assignment, ``del``, ``for``/``async for``, ``with``/``async with``,
walrus in every position a walrus may stand, ``import`` and ``from``
imports, ``except``/``except*`` names, every capturing pattern, nested
``def``/``async def``/``class``, lambdas, comprehensions and generator
expressions, ``global``, ``nonlocal``, and on 3.12+ ``type`` aliases and
PEP 695 type parameters), and compiles each one. CPython is the oracle:

- the symbol table says which names are the function's locals, which it
  declares ``global`` or ``nonlocal``, which it takes free from the
  enclosing function, and which it references;
- the code objects say which names the function stores (every ``STORE_*``
  of a name) and which it, or any scope nested in it, reads (every load of
  a name, and every ``del``, which needs the binding as a load does).

Towel's collectors must agree: the function's locals, its declarations and
its free names; ``own_scope_bindings`` (the reassignment analysis) must
store exactly what the function stores; ``loaded_names`` must read at least
what any of its code reads, and more only through annotations a function
body never evaluates; ``NameCollector`` must read what the function's own
scope, its lambdas, comprehensions and class bodies read. A disagreement is
the defect class behind the bindings audit: a ``for`` target, capture or
nested ``def`` not seen as rebinding, a ``+=`` or ``del`` not seen as a read.

Three normalizations keep the oracle version-proof, and each is narrow:
Python 3.12 inlines list, set and dict comprehensions into the function's
own symbol table and code (PEP 709), so their iteration variables, drawn
from a pool no other construct uses, are set aside; an ``except ... as``
name is deleted by the handler's own cleanup, so deletions of handler
names, which the generator never deletes itself, are set aside; and 3.13
builds closures with plain ``LOAD_FAST``, so the loads that feed a closure
tuple are set aside.
"""

from __future__ import annotations

import ast
import dis
import random
import symtable
import sys
import types
import warnings
from dataclasses import dataclass
from typing import FrozenSet, Iterator, List, Set, Tuple

import pytest

from towel.unification.assignment_analyzer import (
    analyze_assignments,
    has_reassignments_without_bindings,
    own_scope_bindings,
    scope_declarations,
)
from towel.unification.definite_assignment import locally_bound_names
from towel.unification.parameters import parameter_names
from towel.unification.semantic_safety import _own_scope_locals
from towel.unification.statement_facts import bindings_of, loaded_names
from towel.unification.visitors import NameCollector

SEED = 1772
FUNCTIONS = 300

LOCALS = ("a", "b", "c", "d")
"""Names any construct may bind, so constructs rebind each other's names."""
COMPREHENSION_TARGETS = ("k0", "k1")
"""Iteration variables of list, set and dict comprehensions, and nothing else."""
HANDLER_NAMES = ("h0", "h1")
"""``except ... as`` names; read and rebound like any local, never deleted by ``del``."""
READ_ONLY = ("r0", "o0")
"""A module name and a name of the enclosing function; read, never bound here."""
DECLARED_GLOBAL = "g0"
DECLARED_NONLOCAL = "n0"
TYPE_ALIASES = ("T0",)

_COMPREHENSION_CODE = frozenset({"<listcomp>", "<setcomp>", "<dictcomp>", "<genexpr>"})
_STORES = frozenset({"STORE_FAST", "STORE_DEREF", "STORE_GLOBAL", "STORE_NAME"})
_LOADS = frozenset(
    {
        "LOAD_FAST",
        "LOAD_FAST_CHECK",
        "LOAD_DEREF",
        "LOAD_GLOBAL",
        "LOAD_NAME",
        "LOAD_CLASSDEREF",
        "LOAD_FROM_DICT_OR_DEREF",
        "LOAD_FROM_DICT_OR_GLOBALS",
        "DELETE_FAST",
        "DELETE_DEREF",
        "DELETE_GLOBAL",
        "DELETE_NAME",
    }
)


class _Writer:
    """Writes one random function body, as source lines, out of every binding construct."""

    def __init__(self, rng: random.Random, *, is_async: bool) -> None:
        self.rng = rng
        self.is_async = is_async

    def local(self) -> str:
        return self.rng.choice(LOCALS)

    def readable(self) -> str:
        return self.rng.choice(
            LOCALS + HANDLER_NAMES + READ_ONLY + ("p", "q", DECLARED_GLOBAL, DECLARED_NONLOCAL)
        )

    # -- expressions -------------------------------------------------------

    def expression(self, depth: int = 0, *, walrus: bool = True) -> str:
        """An expression; ``walrus`` says whether an assignment expression may stand here."""
        leaf = depth >= 2 or self.rng.random() < 0.35
        if leaf:
            # No constant: a constant test lets the compiler drop the code it guards.
            return self.rng.choice([self.readable(), self.readable(), self.readable(), "h(1)"])
        inner = lambda: self.expression(depth + 1, walrus=walrus)  # noqa: E731
        forms = [
            lambda: f"h({inner()}, {inner()})",
            lambda: f"{inner()}.x",
            lambda: f"{inner()}[0]",
            lambda: f"({inner()} if {inner()} else {inner()})",
            lambda: f"({inner()} and {inner()})",
            lambda: f"({inner()} or {inner()})",
            lambda: f"(not {inner()})",
            lambda: f"({inner()} < {inner()} < {inner()})",
            # The spaces keep a dict display from reading as escaped braces.
            lambda: f"f'{{ {inner()} }}'",
            lambda: f"{{{inner()}: {inner()}, **{inner()}}}",
            lambda: f"[{inner()}, *{inner()}]",
            lambda: f"(lambda x, y={inner()}: x + {self.readable()})",
            lambda: self.comprehension(depth, walrus=walrus),
            lambda: f"({inner()} for {self.local()} in {inner()})",
        ]
        if walrus:
            forms.append(lambda: f"({self.local()} := {inner()})")
        return self.rng.choice(forms)()

    def comprehension(self, depth: int, *, walrus: bool) -> str:
        target = self.rng.choice(COMPREHENSION_TARGETS)
        # The iterable of a comprehension may hold no assignment expression.
        iterable = self.expression(depth + 1, walrus=False)
        element = self.rng.choice(
            [target, self.readable(), f"({self.local()} := {target})" if walrus else target]
        )
        condition = self.rng.choice(["", f" if {target}", f" if {self.readable()}"])
        kind = self.rng.randrange(3)
        if kind == 0:
            return f"[{element} for {target} in {iterable}{condition}]"
        if kind == 1:
            return f"{{{element} for {target} in {iterable}{condition}}}"
        return f"{{{target}: {element} for {target} in {iterable}{condition}}}"

    def target(self) -> str:
        return self.rng.choice(
            [
                self.local(),
                self.local(),
                f"{self.local()}, {self.local()}",
                f"[{self.local()}, *{self.local()}]",
                f"{self.readable()}.x",
                f"{self.readable()}[{self.readable()}]",
            ]
        )

    # -- statements --------------------------------------------------------

    def block(
        self, depth: int, indent: str, *, in_loop: bool = False, loop_body: bool = False
    ) -> List[str]:
        count = self.rng.randint(1, 3 if depth else 6)
        lines: List[str] = []
        for _ in range(count):
            lines.extend(self.statement(depth, indent, in_loop=in_loop))
        # Only the last statement of a loop's body or of the function leaves
        # it, so no code is unreachable and dropped by the compiler.
        if depth == 0 and self.rng.random() < 0.3:
            value = self.expression()
            lines.append(indent + self.rng.choice([f"return {value}", f"raise E({value})"]))
        elif in_loop and loop_body and self.rng.random() < 0.2:
            lines.append(indent + self.rng.choice(["break", "continue"]))
        return lines

    def statement(self, depth: int, indent: str, *, in_loop: bool) -> List[str]:
        simple = [
            lambda: [f"{indent}{self.target()} = {self.expression()}"],
            lambda: [f"{indent}{self.local()} = {self.local()} = {self.expression()}"],
            lambda: [f"{indent}{self.rng.choice(LOCALS + HANDLER_NAMES)} += {self.expression()}"],
            lambda: [f"{indent}{self.readable()}.x += {self.expression()}"],
            lambda: [f"{indent}{self.local()}: {self.readable()} = {self.expression()}"],
            lambda: [f"{indent}{self.local()}: {self.readable()}"],
            lambda: [f"{indent}del {self.local()}"],
            lambda: [f"{indent}del {self.local()}, {self.readable()}[0]"],
            lambda: [f"{indent}h({self.expression()})"],
            lambda: [f"{indent}import m"],
            lambda: [f"{indent}import m.sub"],
            lambda: [f"{indent}import m as {self.local()}"],
            lambda: [f"{indent}from m import {self.local()}"],
            lambda: [f"{indent}from m import zz as {self.local()}"],
            lambda: [f"{indent}{DECLARED_GLOBAL} = {self.expression()}"],
            lambda: [f"{indent}{DECLARED_NONLOCAL} += 1"],
            lambda: [f"{indent}pass"],
        ]
        if sys.version_info >= (3, 12):
            simple.append(
                lambda: [f"{indent}type {self.rng.choice(TYPE_ALIASES)} = {self.readable()}"]
            )
        if depth >= 2:
            return self.rng.choice(simple)()
        inner = indent + "    "
        compound = [
            lambda: [
                f"{indent}for {self.target()} in {self.expression()}:",
                *self.block(depth + 1, inner, in_loop=True, loop_body=True),
                *self.optional_else(depth, indent),
            ],
            lambda: [
                f"{indent}while {self.expression()}:",
                *self.block(depth + 1, inner, in_loop=True, loop_body=True),
                *self.optional_else(depth, indent),
            ],
            lambda: [
                f"{indent}if {self.expression()}:",
                *self.block(depth + 1, inner, in_loop=in_loop),
                *self.optional_else(depth, indent, in_loop=in_loop),
            ],
            lambda: [
                f"{indent}with {self.expression()} as {self.target()}, {self.expression()}:",
                *self.block(depth + 1, inner, in_loop=in_loop),
            ],
            lambda: self.try_statement(depth, indent, in_loop=in_loop),
            lambda: self.match_statement(depth, indent, in_loop=in_loop),
            lambda: self.function_definition(indent),
            lambda: self.class_definition(indent),
        ]
        if self.is_async:
            compound.append(
                lambda: [
                    f"{indent}async for {self.target()} in {self.expression()}:",
                    *self.block(depth + 1, inner, in_loop=True, loop_body=True),
                ]
            )
            compound.append(
                lambda: [
                    f"{indent}async with {self.expression()} as {self.local()}:",
                    *self.block(depth + 1, inner, in_loop=in_loop),
                ]
            )
        choice = self.rng.random()
        return self.rng.choice(compound if choice < 0.45 else simple)()

    def optional_else(self, depth: int, indent: str, *, in_loop: bool = False) -> List[str]:
        if self.rng.random() < 0.5:
            return []
        return [f"{indent}else:", *self.block(depth + 1, indent + "    ", in_loop=in_loop)]

    def try_statement(self, depth: int, indent: str, *, in_loop: bool) -> List[str]:
        inner = indent + "    "
        star = "*" if self.rng.random() < 0.3 else ""
        lines = [f"{indent}try:", *self.block(depth + 1, inner, in_loop=in_loop)]
        lines += [
            f"{indent}except{star} {self.readable()} as {self.rng.choice(HANDLER_NAMES)}:",
            *self.block(depth + 1, inner, in_loop=in_loop and not star),
        ]
        if self.rng.random() < 0.5:
            lines += [
                f"{indent}except{star} E:",
                *self.block(depth + 1, inner, in_loop=in_loop and not star),
            ]
        if self.rng.random() < 0.3:
            lines += [f"{indent}else:", *self.block(depth + 1, inner, in_loop=in_loop)]
        if self.rng.random() < 0.3:
            lines += [f"{indent}finally:", *self.block(depth + 1, inner)]
        return lines

    def match_statement(self, depth: int, indent: str, *, in_loop: bool) -> List[str]:
        inner = indent + "    "
        body = indent + "        "
        patterns = [
            f"[{self.local()}, *{self.local()}]",
            f"{{'k': {self.local()}, **{self.local()}}}",
            f"C(x={self.local()}) as {self.local()}",
            self.alternatives(),
            f"Point.X | {self.local()}.Y",
            f"{self.local()}",
            "_",
        ]
        lines = [f"{indent}match {self.expression()}:"]
        for pattern in self.rng.sample(patterns[:-2], self.rng.randint(1, 3)):
            guard = f" if {self.expression()}" if self.rng.random() < 0.3 else ""
            lines += [
                f"{inner}case {pattern}{guard}:",
                *self.block(depth + 2, body, in_loop=in_loop),
            ]
        if self.rng.random() < 0.5:
            lines += [
                f"{inner}case {self.rng.choice(patterns[-2:])}:",
                *self.block(depth + 2, body, in_loop=in_loop),
            ]
        return lines

    def alternatives(self) -> str:
        """An or-pattern: every alternative binds the same name."""
        name = self.local()
        return f"[1, {name}] | [{name}, 2]"

    def function_definition(self, indent: str) -> List[str]:
        keyword = "async def" if self.rng.random() < 0.2 else "def"
        decorator = [f"{indent}@{self.expression(1)}"] if self.rng.random() < 0.3 else []
        generic = "[T]" if sys.version_info >= (3, 12) and self.rng.random() < 0.2 else ""
        annotation = self.rng.choice(["", f": {self.expression(1)}"])
        returns = self.rng.choice(["", f" -> {self.expression(1)}"])
        inner = indent + "    "
        body = self.rng.choice(
            [
                [f"{inner}return x + {self.readable()}"],
                [f"{inner}{self.local()} = x", f"{inner}return {self.readable()}"],
                [f"{inner}nonlocal {self.local()}", f"{inner}return {self.readable()}"],
            ]
        )
        return [
            *decorator,
            f"{indent}{keyword} {self.local()}{generic}(x{annotation}, y={self.expression(1)}){returns}:",
            *body,
        ]

    def class_definition(self, indent: str) -> List[str]:
        inner = indent + "    "
        return [
            f"{indent}class {self.local()}({self.expression(1, walrus=False)}):",
            f"{inner}{self.local()} = {self.readable()}",
            f"{inner}{self.local()}: {self.readable()}",
            f"{inner}def m(self):",
            f"{inner}    return {self.readable()}",
        ]


@dataclass(frozen=True)
class _Case:
    source: str
    function: ast.FunctionDef | ast.AsyncFunctionDef
    table: symtable.SymbolTable
    code: types.CodeType


def _cases() -> Iterator[_Case]:
    rng = random.Random(SEED)
    written = 0
    attempts = 0
    while written < FUNCTIONS:
        attempts += 1
        assert attempts < FUNCTIONS * 20, "the generator writes too few functions Python accepts"
        is_async = rng.random() < 0.2
        writer = _Writer(rng, is_async=is_async)
        declarations: List[str] = []
        if rng.random() < 0.5:
            declarations.append(f"        global {DECLARED_GLOBAL}")
        if rng.random() < 0.5:
            declarations.append(f"        nonlocal {DECLARED_NONLOCAL}")
        body = writer.block(0, "        ")
        source = "\n".join(
            [
                f"def outer(o0, {DECLARED_NONLOCAL}):",
                f"    {'async def' if is_async else 'def'} f(p, q):",
                *declarations,
                *body,
                "    return f",
                "",
            ]
        )
        try:
            with warnings.catch_warnings():
                # ``1[0]`` and the like compile, with a warning nobody reads here.
                warnings.simplefilter("ignore", SyntaxWarning)
                code = compile(source, "<generated>", "exec", dont_inherit=True)
                table = symtable.symtable(source, "<generated>", "exec")
        except SyntaxError:
            continue
        outer = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef))
        function = next(
            node for node in outer.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        outer_table = next(child for child in table.get_children() if child.get_name() == "outer")
        function_table = next(
            child for child in outer_table.get_children() if child.get_name() == "f"
        )
        outer_code = next(c for c in code.co_consts if isinstance(c, types.CodeType))
        function_code = next(
            c for c in outer_code.co_consts if isinstance(c, types.CodeType) and c.co_name == "f"
        )
        written += 1
        yield _Case(source, function, function_table, function_code)


# -- what CPython says -------------------------------------------------------------


def _names(argval: object) -> Tuple[str, ...]:
    """The name or names an instruction's argument holds (3.13 pairs two in one)."""
    if isinstance(argval, str):
        return (argval,)
    if isinstance(argval, tuple):
        return tuple(name for name in argval if isinstance(name, str))
    return ()


def _closure_loads(instructions: List[dis.Instruction]) -> Set[int]:
    """Offsets of the loads that only build a nested function's closure tuple.

    The tuple is the ``BUILD_TUPLE`` just before the nested code object whose
    loads spell exactly that code's free variables, in order.
    """
    skipped: Set[int] = set()
    for index, instruction in enumerate(instructions):
        if instruction.opname != "BUILD_TUPLE" or index + 1 >= len(instructions):
            continue
        nested = instructions[index + 1].argval
        if not isinstance(nested, types.CodeType) or not nested.co_freevars:
            continue
        names: List[str] = []
        feeding: List[int] = []
        cursor = index - 1
        while cursor >= 0 and len(names) < len(nested.co_freevars):
            load = instructions[cursor]
            if load.opname not in (
                "LOAD_FAST",
                "LOAD_CLOSURE",
                "LOAD_FAST_LOAD_FAST",
                "LOAD_DEREF",
            ):
                break
            names[:0] = list(_names(load.argval))
            feeding.append(load.offset)
            cursor -= 1
        if tuple(names) == nested.co_freevars:
            skipped.update(feeding)
    return skipped


def _instructions(code: types.CodeType) -> List[dis.Instruction]:
    return list(dis.get_instructions(code))


def _stores(code: types.CodeType) -> Set[str]:
    """Names the code object stores, and those its comprehensions store into its cells."""
    return {name for _, name in _stores_by_line(code)}


def _stores_by_line(code: types.CodeType) -> List[Tuple[int, str]]:
    """Each store of a name, with the source line it stands on."""
    found: List[Tuple[int, str]] = []
    for instruction in _instructions(code):
        line = _line(instruction)
        if instruction.opname in _STORES or instruction.opname == "STORE_FAST_STORE_FAST":
            found.extend((line, name) for name in _names(instruction.argval))
        elif instruction.opname == "STORE_FAST_LOAD_FAST":
            found.extend((line, name) for name in _names(instruction.argval)[:1])
    for child in code.co_consts:
        if isinstance(child, types.CodeType) and child.co_name in _COMPREHENSION_CODE:
            # A walrus in a comprehension stores into the function's cell.
            found.extend(
                (line, name) for line, name in _cell_stores(child) if name in code.co_cellvars
            )
    return found


def _line(instruction: dis.Instruction) -> int:
    positions = instruction.positions
    return positions.lineno if positions is not None and positions.lineno is not None else -1


def _cell_stores(code: types.CodeType) -> List[Tuple[int, str]]:
    """Stores a comprehension makes into cells it takes from outside, its nested ones included."""
    found = [
        (_line(instruction), name)
        for instruction in _instructions(code)
        if instruction.opname == "STORE_DEREF"
        for name in _names(instruction.argval)
        if name in code.co_freevars
    ]
    for child in code.co_consts:
        if isinstance(child, types.CodeType) and child.co_name in _COMPREHENSION_CODE:
            found.extend(
                (line, name) for line, name in _cell_stores(child) if name in code.co_freevars
            )
    return found


def _loads(code: types.CodeType, *, into: bool) -> Set[str]:
    """Names the code reads or deletes; with ``into`` also every code object nested in it."""
    instructions = _instructions(code)
    closure = _closure_loads(instructions)
    names: Set[str] = set()
    for instruction in instructions:
        if instruction.offset in closure:
            continue
        if instruction.opname.startswith("DELETE_") and instruction.argval in HANDLER_NAMES:
            continue  # the except clause's own cleanup of its name; no ``del`` of one is written
        if instruction.opname in _LOADS or instruction.opname == "LOAD_FAST_LOAD_FAST":
            names.update(_names(instruction.argval))
        elif instruction.opname == "STORE_FAST_LOAD_FAST":
            names.update(_names(instruction.argval)[1:])
    for child in code.co_consts:
        if isinstance(child, types.CodeType) and (into or not _is_function_body(child)):
            names |= _loads(child, into=into)
    # Dunder names are what a class body reads and writes of its own accord;
    # ``.0`` is the iterator a comprehension is handed.
    return {name for name in names if not name.startswith(("__", "."))}


def _is_function_body(code: types.CodeType) -> bool:
    """A ``def``'s own code: a new-locals scope that is not a lambda, comprehension or alias value."""
    if code.co_name == "<lambda>" or code.co_name in _COMPREHENSION_CODE:
        return False
    if code.co_name in TYPE_ALIASES or code.co_name.startswith("<"):
        return False
    return bool(code.co_flags & 0x02)  # CO_NEWLOCALS: a class body has none


def _inlined_comprehension_names() -> FrozenSet[str]:
    return frozenset(COMPREHENSION_TARGETS) if sys.version_info >= (3, 12) else frozenset()


def _cpython_locals(case: _Case) -> Set[str]:
    return {
        symbol.get_name() for symbol in case.table.get_symbols() if symbol.is_local()
    } - _inlined_comprehension_names()


def _unevaluated_annotation_names(function: ast.AST) -> Set[str]:
    """Names in the annotations of local variables of functions, which no code evaluates."""
    names: Set[str] = set()

    def walk(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AnnAssign) and in_function:
                names.update(loaded_names(child.annotation))
            nested = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            walk(
                child,
                True if nested else (False if isinstance(child, ast.ClassDef) else in_function),
            )

    walk(function, True)
    return names


# -- what Towel says ---------------------------------------------------------------


def _towel_locals(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Set[str]:
    bound: Set[str] = set(parameter_names(function.args))
    for statement in function.body:
        bound |= bindings_of(statement, into_nested_scopes=False)
    return bound - scope_declarations(function)


def _name_collector_reads(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Set[str]:
    collector = NameCollector()
    for statement in function.body:
        collector.visit(statement)
    return collector.used


_CASES = list(_cases())


def _explain(case: _Case, detail: str) -> str:
    return f"{detail}\n--- generated function ---\n{case.source}"


def test_the_generator_covers_every_binding_construct() -> None:
    """Each construct the module docstring lists occurs in the generated functions."""
    kinds: Set[str] = set()
    for case in _CASES:
        for node in ast.walk(case.function):
            kinds.add(type(node).__name__)
    required = {
        "Assign",
        "AugAssign",
        "AnnAssign",
        "Delete",
        "For",
        "While",
        "With",
        "NamedExpr",
        "Import",
        "ImportFrom",
        "ExceptHandler",
        "MatchAs",
        "MatchStar",
        "MatchMapping",
        "MatchOr",
        "MatchClass",
        "MatchValue",
        "FunctionDef",
        "AsyncFunctionDef",
        "ClassDef",
        "Lambda",
        "ListComp",
        "SetComp",
        "DictComp",
        "GeneratorExp",
        "Global",
        "Nonlocal",
        "Starred",
        "AsyncFor",
        "AsyncWith",
        "TryStar",
    }
    if sys.version_info >= (3, 12):
        required |= {"TypeAlias", "TypeVar"}
    assert required <= kinds, sorted(required - kinds)


@pytest.mark.parametrize("index", range(0, FUNCTIONS, 50))
def test_locals_and_declarations_are_the_symbol_tables(index: int) -> None:
    for case in _CASES[index : index + 50]:
        expected = _cpython_locals(case)
        assert _towel_locals(case.function) == expected, _explain(case, "bindings_of")
        assert _own_scope_locals(case.function) == expected, _explain(case, "_own_scope_locals")
        assert locally_bound_names(case.function) >= expected, _explain(case, "locally_bound_names")
        assert not _towel_locals(case.function) & set(COMPREHENSION_TARGETS)
        declared = {
            symbol.get_name()
            for symbol in case.table.get_symbols()
            if symbol.is_declared_global() or symbol.is_nonlocal()
        }
        assert scope_declarations(case.function) == declared, _explain(case, "scope_declarations")


@pytest.mark.parametrize("index", range(0, FUNCTIONS, 50))
def test_the_reassignment_analysis_sees_every_store(index: int) -> None:
    for case in _CASES[index : index + 50]:
        seen = {binding.name for binding in own_scope_bindings(case.function.body)}
        stored = _stores(case.code) - _inlined_comprehension_names()
        assert seen == stored, _explain(case, f"own_scope_bindings {sorted(seen ^ stored)}")


@pytest.mark.parametrize("index", range(0, FUNCTIONS, 50))
def test_the_reassignment_analysis_classifies_every_binding(index: int) -> None:
    """A binding is a reassignment exactly when a parameter or an earlier binding holds the name."""
    for case in _CASES[index : index + 50]:
        classification = analyze_assignments(case.function)
        seen: Set[str] = set(parameter_names(case.function.args))
        for binding in own_scope_bindings(case.function.body):
            expected = binding.reads_first or binding.name in seen
            assert classification[binding.node_id] == expected, _explain(case, binding.name)
            seen.add(binding.name)


@pytest.mark.parametrize("index", range(0, FUNCTIONS, 50))
def test_a_block_that_rebinds_what_cpython_stored_before_it_is_unsafe(index: int) -> None:
    """Every name a block stores that CPython stored before it, or holds as a parameter, is flagged.

    Whatever construct stores it (a ``for`` target, a capture, a ``def``):
    the helper would bind its own local, and a read of the name after a
    rebinding that did not happen would not see the caller's binding.
    """
    for case in _CASES[index : index + 50]:
        body = case.function.body
        stores = [
            (line, name)
            for line, name in _stores_by_line(case.code)
            if name not in _inlined_comprehension_names()
        ]
        classification = analyze_assignments(case.function)
        declared = {
            symbol.get_name()
            for symbol in case.table.get_symbols()
            if symbol.is_declared_global() or symbol.is_nonlocal()
        }
        parameters = set(parameter_names(case.function.args))
        for start in range(len(body)):
            # A decorated definition starts at its first decorator.
            first_line = min(
                [body[start].lineno, *(node.lineno for node in _decorators(body[start]))]
            )
            before = {name for line, name in stores if 0 <= line < first_line} | parameters
            for end in range(start + 1, len(body) + 1):
                block = body[start:end]
                last_line = block[-1].end_lineno or block[-1].lineno
                rebound = {
                    name for line, name in stores if first_line <= line <= last_line
                } & before
                _, flagged = has_reassignments_without_bindings(
                    case.function, block, classification
                )
                missed = rebound - declared - flagged
                assert not missed, _explain(
                    case, f"statements {start}:{end} rebind {sorted(missed)} unflagged"
                )


def _decorators(statement: ast.stmt) -> List[ast.expr]:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return list(statement.decorator_list)
    return []


@pytest.mark.parametrize("index", range(0, FUNCTIONS, 50))
def test_reads_cover_every_load_and_del(index: int) -> None:
    for case in _CASES[index : index + 50]:
        towel = set().union(*(loaded_names(statement) for statement in case.function.body))
        cpython = _loads(case.code, into=True)
        missing = cpython - towel
        assert not missing, _explain(case, f"loaded_names misses {sorted(missing)}")
        extra = towel - cpython - _unevaluated_annotation_names(case.function)
        assert not extra, _explain(case, f"loaded_names invents {sorted(extra)}")
        referenced = {s.get_name() for s in case.table.get_symbols() if s.is_referenced()}
        assert referenced - _inlined_comprehension_names() <= towel, _explain(case, "referenced")


@pytest.mark.parametrize("index", range(0, FUNCTIONS, 50))
def test_the_name_collector_reads_the_functions_own_code(index: int) -> None:
    for case in _CASES[index : index + 50]:
        towel = _name_collector_reads(case.function)
        cpython = _loads(case.code, into=False)
        assert cpython <= towel, _explain(case, f"NameCollector misses {sorted(cpython - towel)}")
        extra = towel - cpython - _unevaluated_annotation_names(case.function)
        assert not extra, _explain(case, f"NameCollector invents {sorted(extra)}")


@pytest.mark.parametrize("index", range(0, FUNCTIONS, 50))
def test_free_names_are_the_symbol_tables(index: int) -> None:
    """The names ``f`` takes from ``outer``: what it or its nested scopes read, and ``nonlocal``."""
    for case in _CASES[index : index + 50]:
        free = {symbol.get_name() for symbol in case.table.get_symbols() if symbol.is_free()}
        reads = set().union(*(loaded_names(statement) for statement in case.function.body))
        outer_names = {"o0", DECLARED_NONLOCAL}
        towel = ((reads - _towel_locals(case.function)) & outer_names) | (
            scope_declarations(case.function) & outer_names
        )
        assert towel == free & outer_names, _explain(case, "free names")
