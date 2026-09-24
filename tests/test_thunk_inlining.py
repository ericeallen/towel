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

"""Unit tests for eager inlining of leading thunks."""

from __future__ import annotations

import ast

import pytest

from tests.test_helpers import function_def
from towel.unification.thunk_inlining import inline_leading_thunks
from towel.unification.substitution import Substitution


def _substitution(*names: str) -> tuple[Substitution, dict[str, int]]:
    substitution = Substitution()
    for name in names:
        substitution.param_expressions[name] = []
        substitution.function_params[name] = []
    return substitution, {name: index for index, name in enumerate(names)}


def _inline(source: str, *names: str) -> tuple[set[str], str]:
    helper = function_def(source)
    substitution, order = _substitution(*names)
    inlined = inline_leading_thunks(helper, substitution, order)
    assert inlined == substitution.inlined_parameters
    assert not (inlined & set(substitution.function_params))
    return inlined, ast.unparse(helper)


@pytest.mark.parametrize(
    "source, names, expected",
    [
        (
            "def h(__param_0):\n    x = __param_0()\n    return x + 1\n",
            ("__param_0",),
            {"__param_0"},
        ),
        ("def h(__param_0, d):\n    d[__param_0()] = 1\n", ("__param_0",), {"__param_0"}),
        (
            "def h(__param_0):\n    return ''.join(sorted(__param_0() | set('ab')))\n",
            ("__param_0",),
            {"__param_0"},
        ),
        (
            "def h(__param_0, xs):\n    for x in __param_0():\n        use(x)\n",
            ("__param_0",),
            {"__param_0"},
        ),
        (
            "def h(__param_0):\n    if __param_0():\n        return 1\n    return 2\n",
            ("__param_0",),
            {"__param_0"},
        ),
        (
            "def h(__param_0, __param_1):\n    a = __param_0()\n    b = __param_1()\n    return a, b\n",
            ("__param_0", "__param_1"),
            {"__param_0", "__param_1"},
        ),
        (
            "def h(__param_0, __param_1):\n    return f(__param_0(), __param_1())\n",
            ("__param_0", "__param_1"),
            {"__param_0", "__param_1"},
        ),
    ],
)
def test_leading_thunks_are_inlined(
    source: str, names: tuple[str, ...], expected: set[str]
) -> None:
    inlined, rendered = _inline(source, *names)
    assert inlined == expected
    for name in expected:
        assert f"{name}()" not in rendered


@pytest.mark.parametrize(
    "source, names",
    [
        # used twice
        (
            "def h(__param_0):\n    if not __param_0():\n        raise E\n    return len(__param_0())\n",
            ("__param_0",),
        ),
        # an effect precedes it
        ("def h(__param_0, counter):\n    counter['total'] += __param_0()\n", ("__param_0",)),
        ("def h(__param_0, obj):\n    return obj.method(__param_0())\n", ("__param_0",)),
        ("def h(__param_0):\n    print('start')\n    return __param_0()\n", ("__param_0",)),
        # conditional or repeated evaluation
        ("def h(__param_0, flag):\n    return flag and __param_0()\n", ("__param_0",)),
        ("def h(__param_0, flag):\n    return 1 if flag else __param_0()\n", ("__param_0",)),
        ("def h(__param_0, xs):\n    return [__param_0() for x in xs]\n", ("__param_0",)),
        ("def h(__param_0):\n    while __param_0():\n        pass\n", ("__param_0",)),
        ("def h(__param_0, a, b):\n    return a < b < __param_0()\n", ("__param_0",)),
        ("def h(__param_0):\n    return lambda: __param_0()\n", ("__param_0",)),
        # exception context would change where it is caught
        (
            "def h(__param_0):\n    try:\n        return __param_0()\n    except E:\n        return None\n",
            ("__param_0",),
        ),
        ("def h(__param_0, cm):\n    with cm:\n        return __param_0()\n", ("__param_0",)),
        # a global declaration is skipped but an effectful first statement still blocks
        ("def h(__param_0):\n    global g\n    g = f()\n    return __param_0()\n", ("__param_0",)),
    ],
)
def test_non_leading_thunks_stay_deferred(source: str, names: tuple[str, ...]) -> None:
    inlined, rendered = _inline(source, *names)
    assert inlined == set()
    for name in names:
        assert f"{name}()" in rendered


def test_first_of_two_is_inlined_when_second_is_not_first() -> None:
    inlined, rendered = _inline(
        "def h(__param_0, __param_1):\n    a = __param_0()\n    print(a)\n    return __param_1()\n",
        "__param_0",
        "__param_1",
    )
    assert inlined == {"__param_0"}
    assert "__param_1()" in rendered


def test_later_parameter_evaluated_first_is_inlined_alone() -> None:
    # The call site evaluates E1 eagerly, then the helper evaluates E0 in place:
    # the same order as the original block.
    inlined, rendered = _inline(
        "def h(__param_0, __param_1):\n    b = __param_1()\n    a = __param_0()\n    return a, b\n",
        "__param_0",
        "__param_1",
    )
    assert inlined == {"__param_1"}
    assert "__param_0()" in rendered and "__param_1()" not in rendered


@pytest.mark.parametrize(
    "source, expected",
    [
        ("def h(__param_0):\n    x: int = __param_0()\n    return x\n", {"__param_0"}),
        ("def h(__param_0, total):\n    total += __param_0()\n    return total\n", {"__param_0"}),
        ("def h(__param_0, d):\n    del d[__param_0()]\n", {"__param_0"}),
        ("def h(__param_0):\n    raise __param_0()\n", {"__param_0"}),
        ("def h(__param_0):\n    with __param_0() as f:\n        f.read()\n", {"__param_0"}),
        (
            "def h(__param_0):\n    match __param_0():\n        case _:\n            pass\n",
            {"__param_0"},
        ),
        ("def h(__param_0):\n    global g\n    g = __param_0()\n", {"__param_0"}),
        ("def h(__param_0):\n    while __param_0():\n        pass\n", set()),
        (
            "def h(__param_0):\n    try:\n        x = __param_0()\n    except E:\n        pass\n",
            set(),
        ),
        ("def h(__param_0):\n    assert __param_0()\n", set()),
        ("def h(__param_0, c):\n    if c:\n        pass\n    x = __param_0()\n", set()),
        ("def h(__param_0, c):\n    for _ in c:\n        pass\n    x = __param_0()\n", set()),
    ],
    ids=[
        "annotated-assignment",
        "augmented-assignment",
        "delete",
        "raise",
        "with-first-context",
        "match-subject",
        "after-a-global-declaration",
        "while-test-is-re-evaluated",
        "try-body-is-not-unconditional",
        "assert-depends-on-optimization",
        "after-a-branch",
        "after-a-loop",
    ],
)
def test_statement_kinds_decide_what_is_evaluated_first(source: str, expected: set[str]) -> None:
    inlined, _ = _inline(source, "__param_0")
    assert inlined == expected
