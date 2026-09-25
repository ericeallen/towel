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

"""What the leading-thunk pass may evaluate before a thunk it passes eagerly.

A thunk passed eagerly is evaluated at the call site, before anything the
helper does, so everything the helper evaluates before it must neither run
code nor raise. The pass once let a lambda's defaults, a set display's
hashing, ``*`` unpacking and a global read through, and the thunk's effect
moved in front of theirs (audit 1.772, P1-3). The table gives the verdict
for every expression form Python has; the property evaluates random
expressions the rule calls effect-free among objects that record every
special method called on them, and requires that none is called and nothing
is raised.
"""

from __future__ import annotations

import ast
import random
import warnings
from typing import Callable, Dict, List, Optional, Set, Tuple

import pytest

from tests.test_thunk_inlining import _inline
from towel.unification.thunk_inlining import _LITERAL_METHODS, effect_free

BOUND = frozenset({"x", "y", "f", "o", "xs", "m"})
"""Names sure to be bound where the table's expressions are evaluated: the helper's parameters."""


# Every form, with each case that decides its verdict differently.
EFFECT_FREE_TABLE: List[Tuple[str, bool]] = [
    # Constant
    ("1", True),
    ("'s'", True),
    ("None", True),
    ("...", True),
    # Name: a parameter is bound; a global or builtin may not be
    ("x", True),
    ("missing", False),
    ("len", False),
    # Tuple and List: building runs nothing; ``*`` iterates
    ("(x, 1)", True),
    ("()", True),
    ("[x, [1]]", True),
    ("(*xs,)", False),
    ("[0, *xs]", False),
    # Set and Dict: inserting hashes, which only a constant does quietly
    ("{1, 'a', None}", True),
    ("{b'a', b'b'}", True),
    ("{x}", False),
    ("{(1, 2)}", False),
    ("{'a', b'a'}", False),
    ("{*xs}", False),
    ("{}", True),
    ("{'k': x, 'j': [y]}", True),
    ("{x: 1}", False),
    ("{0: 1, b'': 2}", False),
    ("{**m}", False),
    ("{'k': 1, **m}", False),
    # Starred: see the displays above and the calls below
    # Lambda: creating it evaluates its defaults and nothing else
    ("lambda: missing", True),
    ("lambda k=1: k", True),
    ("lambda *, k=x: k", True),
    ("lambda k=f(): k", False),
    ("lambda k=missing: k", False),
    ("lambda k=1, *, j={x}: k", False),
    # Attribute: a lookup runs __getattribute__ and descriptors, except a
    # method every Python gives a string or bytes literal
    ("o.attr", False),
    ("''.join", True),
    ("b', '.join", True),
    ("''.removeprefix", False),
    ("''.nonexistent", False),
    ("(1).real", False),
    # Subscript and Slice: __getitem__, even of a literal
    ("x[0]", False),
    ("(1, 2)[0]", False),
    ("x[1:2]", False),
    # Call
    ("f()", False),
    ("f(x)", False),
    ("f(*xs)", False),
    ("f(**m)", False),
    # BinOp
    ("x + 1", False),
    ("1 + 2", False),
    # UnaryOp: a negative number is one literal; any other operator runs code or may raise
    ("-1", True),
    ("+1.5", True),
    ("-1j", True),
    ("-True", False),
    ("-x", False),
    ("not x", False),
    ("~1", False),
    ("-'a'", False),
    # BoolOp, IfExp, Compare: truth tests and comparisons run __bool__ and __lt__
    ("x and y", False),
    ("x if y else 1", False),
    ("x < y", False),
    ("1 < 2 < 3", False),
    # JoinedStr and FormattedValue: joining runs nothing; formatting runs __format__
    ("f'abc'", True),
    ("f'{x}'", False),
    ("f'{1}'", False),
    ("f'{x!r:>{y}}'", False),
    # NamedExpr: binding runs nothing, and the name is bound from then on
    ("(z := 1)", True),
    ("(z := x)", True),
    ("(z := missing)", False),
    ("((z := 1), z)", True),
    ("(z, (z := 1))", False),
    # Comprehensions iterate
    ("[i for i in xs]", False),
    ("{i for i in xs}", False),
    ("{i: i for i in xs}", False),
    ("(i for i in xs)", False),
    # Await, Yield, YieldFrom suspend
    ("await x", False),
    ("(yield x)", False),
    ("(yield from xs)", False),
]


@pytest.mark.parametrize(
    "source, verdict", EFFECT_FREE_TABLE, ids=[s for s, _ in EFFECT_FREE_TABLE]
)
def test_each_expression_form_has_its_verdict(source: str, verdict: bool) -> None:
    assert effect_free(ast.parse(source, mode="eval").body, BOUND) is verdict


def test_the_table_covers_every_expression_form() -> None:
    forms = {type(node) for source, _ in EFFECT_FREE_TABLE for node in ast.walk(ast.parse(source))}
    python_forms = {form for form in ast.expr.__subclasses__() if form.__module__ == "ast"}
    assert python_forms - forms == set()


@pytest.mark.parametrize("kind", [str, bytes])
def test_every_listed_literal_method_exists(kind: type) -> None:
    assert all(hasattr(kind, name) for name in _LITERAL_METHODS[kind])


# -- In the helper -------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        # The four shapes the audit found.
        "def h(__param_0):\n    g = lambda k=tr(1): k\n    return __param_0()\n",
        "def h(__param_0, x):\n    seen = {x}\n    return __param_0()\n",
        "def h(__param_0, x):\n    items = [*x]\n    return __param_0()\n",
        "def h(__param_0):\n    base = NOT_DEFINED_YET\n    return __param_0()\n",
        # And their relatives.
        "def h(__param_0, x):\n    d = {x: 1}\n    return __param_0()\n",
        "def h(__param_0, m):\n    d = {**m}\n    return __param_0()\n",
        "def h(__param_0, x):\n    t = (*x, 0)\n    return __param_0()\n",
        "def h(__param_0, f, m):\n    return f(**m, k=__param_0())\n",
        "def h(__param_0, f, xs):\n    return f(*xs, __param_0())\n",
        "def h(__param_0):\n    return len(__param_0())\n",
        "def h(__param_0):\n    return ''.join(sorted(__param_0()))\n",
        "def h(__param_0, x):\n    s = f'{x}'\n    return __param_0()\n",
        # A target list unpacks, which iterates and may raise ValueError.
        "def h(__param_0, pair):\n    a, b = pair\n    return __param_0()\n",
        # Deleting a name may raise, and unbinds it.
        "def h(__param_0):\n    x = 1\n    del x\n    return __param_0()\n",
        # An augmented assignment reads its target first: here a global.
        "def h(__param_0):\n    global g\n    g += __param_0()\n",
        # A local read before it is bound raises UnboundLocalError.
        "def h(__param_0):\n    y = later\n    later = 1\n    return __param_0()\n",
        # Nothing after a return or a raise runs, and the raise is an effect.
        "def h(__param_0, e):\n    raise e\n    y = __param_0()\n",
        "def h(__param_0, x):\n    return x\n    y = __param_0()\n",
    ],
)
def test_a_thunk_after_an_effect_stays_deferred(source: str) -> None:
    inlined, rendered = _inline(source, "__param_0")
    assert inlined == set()
    assert "__param_0()" in rendered


@pytest.mark.parametrize(
    "source",
    [
        "def h(__param_0, x):\n    seen = {1, 'a'}\n    y = x\n    return __param_0()\n",
        "def h(__param_0):\n    g = lambda k=1: k\n    return __param_0()\n",
        "def h(__param_0, x):\n    pair = (x, [x], {'k': x})\n    return __param_0()\n",
        "def h(__param_0):\n    a = -1\n    b = a\n    return __param_0()\n",
        "def h(__param_0, f):\n    return f(__param_0())\n",
        "def h(__param_0):\n    return ''.join(__param_0())\n",
        "def h(__param_0, *args, **kwargs):\n    t = (args, kwargs)\n    return __param_0()\n",
        "def h(__param_0, total):\n    total += __param_0()\n    return total\n",
        "def h(__param_0):\n    s = f'label'\n    n = (k := 2)\n    return __param_0()\n",
    ],
)
def test_a_thunk_after_only_effect_free_steps_is_inlined(source: str) -> None:
    inlined, rendered = _inline(source, "__param_0")
    assert inlined == {"__param_0"}, rendered
    assert "__param_0()" not in rendered


# -- The property --------------------------------------------------------------

CALLED: List[str] = []
"""Every special method, and every other attribute, a ``Tracer`` was asked for."""


class Tracer:
    """An object that records every operation the interpreter performs on it."""

    def __getattr__(self, name: str) -> Tracer:
        CALLED.append(name)
        return Tracer()


def _recording(name: str, result: Callable[[], object]) -> Callable[..., object]:
    def method(self: Tracer, *args: object) -> object:
        CALLED.append(name)
        return result()

    return method


_RESULTS: Dict[str, Callable[[], object]] = {
    "__hash__": lambda: 0,
    "__bool__": lambda: True,
    "__len__": lambda: 0,
    "__index__": lambda: 0,
    "__iter__": lambda: iter(()),
    "__str__": lambda: "t",
    "__repr__": lambda: "t",
    "__format__": lambda: "t",
    "keys": lambda: [],
    **{
        name: lambda: False for name in ("__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__")
    },
    **{
        f"__{prefix}{operation}__": Tracer
        for prefix in ("", "r")
        for operation in ("add", "sub", "mul", "truediv", "mod", "or", "and", "matmul", "pow")
    },
    **{name: Tracer for name in ("__getitem__", "__call__", "__neg__", "__pos__", "__invert__")},
}
for _name, _result in _RESULTS.items():
    setattr(Tracer, _name, _recording(_name, _result))


class _ExpressionWriter:
    """Random expressions over two bound parameters, an unbound global, and a walrus target."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def leaf(self) -> str:
        return self._rng.choice(
            ["p", "q", "p", "missing", "z", "1", "-2", "'s'", "b'b'", "None", "0.5", "True"]
        )

    def expression(self, depth: int) -> str:
        if depth <= 0 or self._rng.random() < 0.25:
            return self.leaf()

        def e() -> str:
            return self.expression(depth - 1)

        forms: List[Callable[[], str]] = [
            lambda: f"({e()}, {e()})",
            lambda: f"[{e()}, {e()}]",
            lambda: f"{{{e()}, {e()}}}",
            lambda: f"{{{e()}: {e()}}}",
            lambda: f"{{{e()}: {e()}, **{e()}}}",
            lambda: f"[*{e()}]",
            lambda: f"({e()}, *{e()})",
            lambda: f"(lambda: {e()})",
            lambda: f"(lambda k={e()}: k)",
            lambda: f"(lambda *, k={e()}: k)",
            lambda: f"{e()}.attr",
            lambda: "''.join",
            lambda: "''.removeprefix",
            lambda: f"{e()}[{e()}]",
            lambda: f"{e()}[{e()}:{e()}]",
            lambda: f"{e()}({e()})",
            lambda: f"{e()}(*{e()})",
            lambda: f"{e()}(**{e()})",
            lambda: f"({e()} + {e()})",
            lambda: f"(-{e()})",
            lambda: f"(not {e()})",
            lambda: f"({e()} and {e()})",
            lambda: f"({e()} if {e()} else {e()})",
            lambda: f"({e()} < {e()})",
            lambda: f"f'{{{e()}}}'",
            lambda: "f'plain'",
            lambda: f"(z := {e()})",
            lambda: f"[i for i in {e()}]",
        ]
        return self._rng.choice(forms)()


def _evaluate(source: str) -> Tuple[List[str], str]:
    """What evaluating ``source`` with tracers for ``p`` and ``q`` called on them, and raised."""
    namespace: Dict[str, object] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        exec(compile(f"def probe(p, q):\n    return {source}\n", "<probe>", "exec"), namespace)
    probe = namespace["probe"]
    assert callable(probe)
    CALLED.clear()
    raised = ""
    try:
        probe(Tracer(), Tracer())
    except Exception as error:
        raised = f"{type(error).__name__}: {error}"
    return list(CALLED), raised


def _parsed(source: str) -> Optional[ast.expr]:
    """``source`` parsed, or None where it is not valid in a function on this Python.

    The writer does not track the rules an assignment expression or an
    f-string's quotes must follow; the compiler does.
    """
    try:
        with warnings.catch_warnings():
            # ``[1][p, q]`` compiles with a warning that it will fail, which it may.
            warnings.simplefilter("ignore", SyntaxWarning)
            compile(f"def probe(p, q):\n    return {source}\n", "<probe>", "exec")
    except SyntaxError:
        return None
    return ast.parse(source, mode="eval").body


def test_what_is_judged_effect_free_calls_nothing_and_raises_nothing() -> None:
    rng = random.Random(1772)
    writer = _ExpressionWriter(rng)
    judged_free: Set[str] = set()
    for _ in range(10000):
        source = writer.expression(3)
        expression = _parsed(source)
        if expression is None or not effect_free(expression, {"p", "q"}):
            continue
        called, raised = _evaluate(source)
        assert not called and not raised, f"{source!r} called {called} and raised {raised!r}"
        judged_free.add(source)
    # Not vacuous: hundreds of distinct expressions, some of them nested displays and lambdas.
    assert len(judged_free) > 300, len(judged_free)
    assert any("lambda k=" in source and "{" in source for source in judged_free)
