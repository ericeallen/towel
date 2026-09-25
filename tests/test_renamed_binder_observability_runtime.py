"""A binder the observability analysis calls always bound is never found unbound when the code runs.

``observable_renamings`` decides whether blocks may share a helper spelled
like one of them: a binder's name reaches the program only through
``UnboundLocalError`` or ``NameError`` when it is read, augmented, deleted
or read by a closure while unbound. The analysis walks the block in
evaluation order to find those reads. This test runs what it analyses: a
seeded generator writes small functions out of every construct that binds,
unbinds or reads a local on some paths only (branches, loops that may not
run, ``del``, ``except ... as`` and ``except* ... as``, ``match`` captures, walrus under ``and`` and
``or``, conditional expressions, ``with`` and ``contextlib.suppress``,
``try``/``finally``, lambdas, comprehensions and nested functions), calls
each many times on random inputs, and asserts that no ``UnboundLocalError``
or ``NameError`` ever names a binder the analysis did not report.
"""

from __future__ import annotations

import ast
import contextlib
import random
import re
from typing import Callable, Dict, List, Set

from towel.unification.instantiation import observable_renamings

SEED = 1772
FUNCTIONS = 1000
RUNS = 16
BINDERS = ("a", "b", "c")


class _Writer:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def name(self) -> str:
        return self.rng.choice(BINDERS)

    def value(self) -> str:
        return self.rng.choice([self.name(), self.name(), "1", "h(p)"])

    def condition(self, *, ends: bool = False) -> str:
        """A test; with ``ends`` one that turns false once ``t`` does, so a loop stops."""
        forms = [
            "t()",
            f"t() and ({self.name()} := 1)",
            f"({self.name()} := t()) or t()",
        ]
        if not ends:
            forms += [
                f"not ({self.name()} := t())",
                f"({self.value()} if t() else {self.value()})",
                f"(t() < ({self.name()} := 1) < {self.value()})",
            ]
        return self.rng.choice(forms)

    def block(self, depth: int, indent: str) -> List[str]:
        lines: List[str] = []
        for _ in range(self.rng.randint(1, 3 if depth else 6)):
            lines.extend(self.statement(depth, indent))
        return lines

    def statement(self, depth: int, indent: str) -> List[str]:
        simple = [
            lambda: [f"{indent}{self.name()} = {self.value()}"],
            lambda: [f"{indent}{self.name()} += 1"],
            lambda: [f"{indent}del {self.name()}"],
            lambda: [f"{indent}seen.append({self.value()})"],
            lambda: [f"{indent}seen.append(({self.name()} := h(p)))"],
            lambda: [f"{indent}seen.append({self.value()} if {self.condition()} else 0)"],
            lambda: [f"{indent}get = lambda: {self.name()}", f"{indent}seen.append(get())"],
            lambda: [f"{indent}seen.append([{self.name()} for _ in p])"],
            lambda: [f"{indent}seen.append(sum({self.name()} for _ in p))"],
            lambda: [f"{indent}if t():", f"{indent}    raise E()"],
            lambda: [f"{indent}assert {self.condition()} or True, {self.value()}"],
        ]
        if depth >= 2:
            return self.rng.choice(simple)()
        inner = indent + "    "
        compound = [
            lambda: [f"{indent}if {self.condition()}:", *self.block(depth + 1, inner)]
            + self.orelse(depth, indent),
            lambda: [f"{indent}for {self.name()} in p:", *self.block(depth + 1, inner)]
            + self.orelse(depth, indent),
            lambda: [
                f"{indent}while {self.condition(ends=True)}:",
                *self.block(depth + 1, inner),
            ],
            lambda: [
                f"{indent}try:",
                *self.block(depth + 1, inner),
                f"{indent}except{self.rng.choice(['', '', '*'])} E as {self.name()}:",
                *self.block(depth + 1, inner),
                *(
                    [f"{indent}finally:", *self.block(depth + 1, inner)]
                    if self.rng.random() < 0.3
                    else []
                ),
            ],
            lambda: [
                f"{indent}with {self.rng.choice(['ctx()', 'suppress(E)'])} as {self.name()}:",
                *self.block(depth + 1, inner),
            ],
            lambda: [
                # An exception the manager swallows skips the rest of the body.
                f"{indent}with suppress(E):",
                f"{inner}if t():",
                f"{inner}    raise E()",
                *self.block(depth + 1, inner),
            ],
            lambda: [
                f"{indent}match p:",
                f"{inner}case [{self.name()}]:",
                *self.block(depth + 2, inner + "    "),
                f"{inner}case [{self.name()}, *{self.name()}]:",
                *self.block(depth + 2, inner + "    "),
                *(
                    [f"{inner}case {self.name()}:", *self.block(depth + 2, inner + "    ")]
                    if self.rng.random() < 0.5
                    else []
                ),
            ],
            lambda: [
                f"{indent}if t():",
                f"{inner}def g():",
                f"{inner}    return {self.name()}",
                f"{inner}seen.append(g())",
            ],
        ]
        return self.rng.choice(compound if self.rng.random() < 0.45 else simple)()

    def orelse(self, depth: int, indent: str) -> List[str]:
        if self.rng.random() < 0.5:
            return []
        return [f"{indent}else:", *self.block(depth + 1, indent + "    ")]


class E(Exception):
    pass


class _Context:
    def __enter__(self) -> int:
        return 0

    def __exit__(self, *exc_info: object) -> None:
        return None


def _environment(rng: random.Random) -> Dict[str, object]:
    budget = [8]

    def t() -> bool:
        # A coin that comes up false once it has come up true often enough.
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return rng.random() < 0.5

    def h(value: object) -> object:
        return value

    return {
        "t": t,
        "h": h,
        "E": E,
        "ctx": _Context,
        "suppress": contextlib.suppress,
        "__builtins__": __builtins__,
    }


def test_no_run_finds_a_binder_unbound_where_the_analysis_says_it_is_bound() -> None:
    rng = random.Random(SEED)
    checked = 0
    observed_unbound = 0
    while checked < FUNCTIONS:
        body = _Writer(rng).block(0, "    ")
        source = "\n".join(["def f(p, seen):", *body, "    return seen", ""])
        try:
            tree = ast.parse(source)
            code = compile(tree, "<generated>", "exec")
        except SyntaxError:
            continue
        function_node = tree.body[0]
        assert isinstance(function_node, ast.FunctionDef)
        claimed = set(observable_renamings(function_node.body, set(BINDERS)))
        checked += 1
        for _ in range(RUNS):
            namespace = _environment(random.Random(rng.random()))
            exec(code, namespace)  # nosec B102: the test's own generated code
            function: Callable[[List[int], List[object]], object] = namespace["f"]  # type: ignore[assignment]
            argument = [rng.randrange(3) for _ in range(rng.randrange(3))]
            try:
                function(argument, [])
            except (UnboundLocalError, NameError) as error:
                # "cannot access local variable 'a' ...", "... free variable 'a' ...":
                # the message is where the name reaches the program.
                quoted = re.search(r"'([^']+)'", str(error))
                name = quoted.group(1) if quoted else None
                assert name in BINDERS, (source, error)
                observed_unbound += 1
                assert name in claimed, f"{name} found unbound, analysis said bound\n{source}"
            except (E, TypeError, AssertionError):
                pass
    # The generator does reach unbound reads, so the check is not vacuous.
    assert observed_unbound > FUNCTIONS // 4


def test_the_analysis_is_not_vacuous() -> None:
    """Some generated functions are claimed unobservable in every binder, and are exercised."""
    rng = random.Random(SEED + 1)
    quiet = 0
    for _ in range(FUNCTIONS):
        body = _Writer(rng).block(0, "    ")
        source = "\n".join(["def f(p, seen):", *body, "    return seen", ""])
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        function_node = tree.body[0]
        assert isinstance(function_node, ast.FunctionDef)
        claimed: Set[str] = set(observable_renamings(function_node.body, set(BINDERS)))
        quiet += not claimed
    assert quiet > FUNCTIONS // 20
