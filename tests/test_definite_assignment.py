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

"""Definite-assignment analysis used to decide whether a free variable may be read eagerly."""

from __future__ import annotations

import ast
from typing import cast

import pytest

from towel.unification.definite_assignment import definitely_bound_before


def _bound_at_marker(source: str) -> set[str]:
    """Names definitely bound before the statement ``marker()`` in the function."""
    function = cast(ast.FunctionDef, ast.parse(source).body[0])
    marker = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "marker"
    )
    return definitely_bound_before(function, marker)


@pytest.mark.parametrize(
    "source, bound, unbound",
    [
        ("def f(a, *r, k=1, **kw):\n    x = 1\n    marker()\n", {"a", "r", "k", "kw", "x"}, set()),
        (
            "def f(c):\n    if c:\n        x = 1\n    else:\n        x = 2\n        y = 3\n    marker()\n",
            {"x"},
            {"y"},
        ),
        (
            "def f(c):\n    if c:\n        return 1\n    else:\n        x = 2\n    marker()\n",
            {"x"},
            set(),
        ),
        ("def f(c):\n    if c:\n        x = 1\n    marker()\n", set(), {"x"}),
        (
            "def f(g):\n    try:\n        ret = g()\n    except E as err:\n        ret = None\n    marker()\n",
            {"ret"},
            {"err"},
        ),
        (
            "def f(g):\n    try:\n        ret = g()\n    except E as err:\n        pass\n    marker()\n",
            set(),
            {"ret", "err"},
        ),
        (
            "def f(g):\n    try:\n        ret = g()\n    except E:\n        raise\n    marker()\n",
            {"ret"},
            set(),
        ),
        (
            "def f(g):\n    try:\n        ret = g()\n    finally:\n        done = True\n    marker()\n",
            {"ret", "done"},
            set(),
        ),
        ("def f(xs):\n    for i in xs:\n        last = i\n    marker()\n", set(), {"i", "last"}),
        ("def f(xs):\n    for i in xs:\n        marker()\n", {"i"}, set()),
        (
            "def f(cm):\n    with cm as h:\n        data = h.read()\n    marker()\n",
            {"h", "data"},
            set(),
        ),
        ("def f(cm):\n    with suppress(E):\n        data = 1\n    marker()\n", set(), {"data"}),
        (
            "def f(v):\n    match v:\n        case [a]:\n            r = 1\n        case _:\n            r = 2\n    marker()\n",
            {"r"},
            {"a"},
        ),
        (
            "def f(v):\n    match v:\n        case [a]:\n            r = 1\n    marker()\n",
            set(),
            {"r"},
        ),
        (
            "def f():\n    import os\n    from sys import argv as av\n    def g():\n        pass\n    marker()\n",
            {"os", "av", "g"},
            set(),
        ),
        ("def f():\n    x = 1\n    del x\n    marker()\n", set(), {"x"}),
        (
            "def f(g):\n    try:\n        ret = g()\n    except E as err:\n        ret = None\n    else:\n        ok = True\n    marker()\n",
            {"ret"},
            {"ok"},
        ),
        ("def f(g):\n    while g():\n        x = 1\n    marker()\n", set(), {"x"}),
        (
            "def f(xs):\n    for i in xs:\n        y = i\n        if y:\n            marker()\n",
            {"i", "y"},
            set(),
        ),
    ],
)
def test_definitely_bound(source: str, bound: set[str], unbound: set[str]) -> None:
    result = _bound_at_marker(source)
    assert bound <= result
    assert not (unbound & result)


@pytest.mark.parametrize(
    "source, bound, unbound",
    [
        # ``except E as e`` deletes ``e`` on handler exit, even when it was
        # bound before the try, so it is not definite after the statement.
        (
            "def f(v):\n    e = 'orig'\n    try:\n        r = 1 / v\n"
            "    except ZeroDivisionError as e:\n        r = None\n    marker()\n",
            {"r"},
            {"e"},
        ),
        (
            "def f(v):\n    try:\n        r = 1 / v\n"
            "    except ZeroDivisionError as e:\n        r = None\n        marker()\n",
            {"r", "e"},
            set(),
        ),
        # A conditional ``del`` leaves the name unbound on one path.
        (
            "def f(c):\n    x = 1\n    y = 2\n    if c:\n        del x\n    marker()\n",
            {"y"},
            {"x"},
        ),
        # The handler's deletion is undone by a later rebinding.
        (
            "def f(v):\n    e = 'orig'\n    try:\n        r = 1 / v\n"
            "    except ZeroDivisionError as e:\n        r = None\n    e = 'again'\n    marker()\n",
            {"r", "e"},
            set(),
        ),
    ],
)
def test_except_handler_name_is_unbound_after_the_handler(
    source: str, bound: set[str], unbound: set[str]
) -> None:
    result = _bound_at_marker(source)
    assert bound <= result
    assert not (unbound & result)
    assert bound <= result
    assert not (unbound & result)


def test_locally_bound_names_excludes_nested_scopes_and_free_names() -> None:
    from towel.unification.definite_assignment import locally_bound_names

    function = cast(
        ast.FunctionDef,
        ast.parse(
            "def f(a, *rest):\n"
            "    import os\n"
            "    for i in xs:\n"
            "        y = i + outer\n"
            "    def inner(q):\n"
            "        z = q\n"
            "    try:\n"
            "        pass\n"
            "    except E as err:\n"
            "        pass\n"
            "    lam = lambda w: w\n"
            "    return helper(y)\n"
        ).body[0],
    )
    bound = locally_bound_names(function)
    assert {"a", "rest", "os", "i", "y", "inner", "err", "lam"} <= bound
    assert not ({"xs", "outer", "q", "z", "w", "helper", "E"} & bound)


@pytest.mark.parametrize(
    "source, unbound",
    [
        # A del later in a loop's body unbinds the name for the next iteration.
        ("def f(xs):\n    x = 1\n    for i in xs:\n        marker()\n        del x\n", {"x"}),
        ("def f(c):\n    x = 1\n    while c():\n        marker()\n        del x\n", {"x"}),
        # ... and for the else clause, which runs after the last iteration.
        (
            "def f(xs):\n    x = 1\n    for i in xs:\n        del x\n        x = 2\n        del x\n"
            "    else:\n        marker()\n",
            {"x"},
        ),
        # A del in an earlier statement of the same list.
        ("def f(c):\n    x = 1\n    if c:\n        del x\n        marker()\n", {"x"}),
        # A handler or finally clause may start after a del in the try.
        (
            "def f(g):\n    x = 1\n    try:\n        del x\n        g()\n    except E:\n        marker()\n",
            {"x"},
        ),
        (
            "def f(g):\n    x = 1\n    try:\n        del x\n        g()\n    finally:\n        marker()\n",
            {"x"},
        ),
        # A body may delete what its with target, pattern or try bound.
        ("def f(cm):\n    with cm as h:\n        del h\n    marker()\n", {"h"}),
        (
            "def f(v):\n    match v:\n        case [a]:\n            del a\n        case a:\n            pass\n"
            "    marker()\n",
            {"a"},
        ),
        (
            "def f(g):\n    try:\n        x = g()\n    except E:\n        x = 0\n    else:\n        del x\n"
            "    marker()\n",
            {"x"},
        ),
        (
            "def f(g):\n    try:\n        x = g()\n    finally:\n        del x\n        x = 1\n        del x\n"
            "    marker()\n",
            {"x"},
        ),
        # except* deletes its name as except does.
        (
            "def f(g):\n    e = 1\n    try:\n        g()\n    except* E as e:\n        pass\n    marker()\n",
            {"e"},
        ),
    ],
)
def test_a_name_unbound_on_some_path_to_the_statement_is_not_definite(
    source: str, unbound: set[str]
) -> None:
    assert not (unbound & _bound_at_marker(source))
