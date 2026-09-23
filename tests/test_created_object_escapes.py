"""Which objects a block creates may move into a helper: only those nothing can look at.

A function, lambda, generator or class made by moved code is made by the
helper, and carries the helper's name in its ``__qualname__`` (a lambda made
from a unified template also has the template's parameter names). The guard
lets a block move only when every such object is called in the block with
arguments it accepts, handed to a builtin that calls it and keeps nothing,
or consumed where it stands.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from towel.unification.scope_analyzer import ScopeAnalyzer
from towel.unification.semantic_safety import _binds, created_object_escapes


def _escapes(body: str, *, after: str = "", module: str = "") -> bool:
    """Whether the block ``body`` of ``def f(xs, k, flag)`` creates an observable object."""
    block = textwrap.indent(textwrap.dedent(body).strip(), "    ")
    rest = textwrap.indent(textwrap.dedent(after).strip(), "    ") if after else ""
    source = f"{textwrap.dedent(module)}\ndef f(xs, k, flag):\n{block}\n{rest}\n    return None\n"
    tree = ast.parse(source)
    analyzer = ScopeAnalyzer()
    analyzer.analyze(tree)
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "f"
    )
    count = len(ast.parse(textwrap.dedent(body).strip()).body)
    return created_object_escapes(analyzer, function, function.body[:count])


@pytest.mark.parametrize(
    "body",
    [
        "g = lambda v: v * k\ny = g(3)",
        "y = (lambda: k * 2)()",
        "y = sorted(xs, key=lambda x: x * k)",
        "y = max(xs, key=lambda x: -x)",
        "y = list(map(lambda x: x + k, xs))",
        "for x in filter(lambda x: x > k, xs):\n    print(x)",
        "y = sum(x * k for x in xs)",
        "y = ', '.join(str(x) for x in xs)",
        "y = next((x for x in xs if x), None)",
        "y = [z for z in (x * 2 for x in xs)]",
        "for i, x in enumerate(x for x in xs):\n    print(i, x)",
        "y = k in (x * 2 for x in xs)",
        "a, b = (x for x in xs)",
        "print(*(x for x in xs))",
        "if flag:\n    def step(v, by=k):\n        return v + by\n    y = step(step(1), by=2)",
        "if flag:\n    def fact(n):\n        return 1 if n < 2 else n * fact(n - 1)\n    y = fact(5)",
        "y = [x * 2 for x in xs]",
        "gen = (x for x in xs)\ny = list(gen)",
        "big = (x for x in xs if x)\nsquares = (x * x for x in big)\ny = sum(squares)",
        "twice = lambda x: x * 2\ny = list(map(twice, xs))",
        "by = lambda x: -x\ny = sorted(xs, key=by)",
        "kept = filter(lambda x: x > k, xs)\ny = [x for x in kept]",
    ],
)
def test_an_object_nothing_can_look_at_may_move(body: str) -> None:
    assert not _escapes(body)


@pytest.mark.parametrize(
    ("body", "after"),
    [
        ("g = lambda: k + 1\nprint(g())", "return g"),
        ("g = lambda v: v * k\ny = g(3)", "print(g)"),
        ("g = lambda v: v * k\nprint(g)", ""),
        ("g = lambda v: v * k\ny = g(value=3)", ""),
        ("g = lambda v: v * k\ny = g(1, 2)", ""),
        ("g = lambda v: v * k\ny = g(*xs)", ""),
        ("kept = [lambda j=j: j for j in xs]", ""),
        ("kept = {}\nkept['f'] = lambda: k", ""),
        ("d = dict(default=lambda: 0)", ""),
        ("y = apply(lambda: k)", ""),
        ("y = sorted(xs, key=lambda a, b: a)", ""),
        ("xs.sort(key=lambda x: x)", ""),
        ("y = min(xs, lambda: 0)", ""),
        ("gen = (x for x in xs)\nprint(gen)", ""),
        ("gen = (x for x in xs)\ny = list(gen)", "print(gen)"),
        ("gen = (x for x in gen)\ny = list(gen)", ""),
        ("y = max(0, (x for x in xs))", ""),
        ("y = sep.join(x for x in xs)", ""),
        ("y = map(lambda x: x, xs)", "return y"),
        ("twice = lambda x: x * 2\ny = list(map(twice, xs, xs))", ""),
        ("twice = lambda x: x * 2\ny = [twice]", ""),
        ("if flag:\n    def step(v):\n        return v + k\n    y = step", ""),
        ("if flag:\n    @wraps\n    def step(v):\n        return v\n    y = step(1)", ""),
        ("if flag:\n    def step():\n        yield k\n    y = list(step())", ""),
        ("if flag:\n    async def step():\n        return k\n    y = 1", ""),
        ("if flag:\n    class Box:\n        pass\n    y = 1", ""),
        ("if flag:\n    def step():\n        return lambda: k\n    y = step()", ""),
    ],
)
def test_an_object_that_could_be_looked_at_declines(body: str, after: str) -> None:
    assert _escapes(body, after=after)


def test_a_builtin_the_module_can_shadow_is_not_trusted() -> None:
    body = "y = sorted(xs, key=lambda x: x)"
    assert _escapes(body, module="def sorted(xs, key):\n    return [key]")
    assert _escapes(body, module="from helpers import *")
    assert not _escapes(body, module="import os")


def test_a_name_declared_nonlocal_or_global_is_a_store_elsewhere() -> None:
    assert _escapes("global g\ng = lambda: k\ny = g()")


SIGNATURES = [
    "",
    "a",
    "a, b=1",
    "a, /, b",
    "a, /, **kw",
    "*, a",
    "*, a=1",
    "a, *args",
    "a=1, *, b",
    "a, b, /, c=2, *, d, e=3, **kw",
    "*args, **kw",
]
CALLS = [
    (0, ()),
    (1, ()),
    (2, ()),
    (3, ()),
    (1, ("a",)),
    (0, ("a",)),
    (1, ("b",)),
    (0, ("a", "b")),
    (2, ("d",)),
    (3, ("d",)),
    (1, ("z",)),
    (0, ("d", "e")),
    (2, ("c", "d")),
    (0, ("c",)),
]


@pytest.mark.parametrize("signature", SIGNATURES)
def test_binding_is_decided_as_python_decides_it(signature: str) -> None:
    function = eval(f"lambda {signature}: None")  # noqa: S307 - a literal lambda
    lambda_node = ast.parse(f"lambda {signature}: None", mode="eval").body
    assert isinstance(lambda_node, ast.Lambda)
    for positional, keywords in CALLS:
        try:
            inspect.signature(function).bind(*range(positional), **dict.fromkeys(keywords, 0))
            expected = True
        except TypeError:
            expected = False
        assert _binds(lambda_node.args, positional, keywords) is expected, (positional, keywords)
