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

"""Original strict-regression programs with relaxed behavioral drivers (review item 5)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    source: str
    driver: str


CASES = (
    Case(
        "test_stored_lambda_parameter_spelling_is_preserved",
        "def first(items):\n    double = lambda value: value * 2\n    out = []\n    for item in items:\n        out.append(double(item))\n    return out\n\ndef second(items):\n    double = lambda other: other * 2\n    out = []\n    for item in items:\n        out.append(double(item))\n    return out\n",
        "print(first([1, -2, 3]), second([4, 0]))",
    ),
    Case(
        "test_dce_with_async_nested_functions_inserts_into_enclosing_async_outer",
        "\nimport asyncio\n\nasync def outer(a, b):\n    async def f1(x):\n        p = x + a\n        q = p * b\n        return q\n\n    async def f2(x):\n        p = x + a\n        q = p * b\n        return q\n\n    return await f1(3) + await f2(4)\n",
        "print(asyncio.run(outer(2, 5)))",
    ),
    Case(
        "test_dce_mixed_async_sync_within_same_async_outer",
        "\nimport asyncio\n\nasync def outer(a, b):\n    async def f1(x):\n        p = x + a\n        q = p * b\n        return q\n\n    def f2(x):\n        p = x + a\n        q = p * b\n        return q\n\n    return await f1(3) + f2(4)\n",
        "print(asyncio.run(outer(2, 5)))",
    ),
    Case(
        "test_dce_keeps_nonlocal_access_at_the_site",
        "\ndef outer():\n    x = 0\n    def f1():\n        nonlocal x\n        t = x + 1\n        u = t * 2\n        return u\n    def f2():\n        nonlocal x\n        t = x + 1\n        u = t * 2\n        return u\n    return f1() + f2()\n",
        "print(outer())",
    ),
    Case(
        "test_clustered_sites_keep_their_own_spelling_of_the_returned_name",
        "v = 5\nw = 5\n\ndef a():\n    tmp = v + 1\n    base = tmp - 3\n    total = base * 2\n    return f'a {total!s}'\n\ndef b():\n    tmp = v + 1\n    base = tmp - 3\n    total = base * 2\n    return f'b {total!r}'\n\ndef c():\n    step = v + 1\n    offset = step - 3\n    amount = offset * 2\n    return f'c {amount!a}'",
        "v = 3\nw = 11\nprint(a(), b(), c())",
    ),
    Case(
        "test_the_helper_returns_what_every_clustered_site_reads",
        "v = 5\nw = 5\n\ndef a():\n    tmp = v + 1\n    base = tmp - 3\n    total = base * 2\n    return f'a {total}'\n\ndef b():\n    tmp = v + 1\n    base = tmp - 3\n    total = base * 2\n    return f'b {total}'\n\ndef c():\n    tmp = v + 1\n    base = tmp - 3\n    total = base * 2\n    return f'c {total} {tmp}'",
        "v = 3\nw = 11\nprint(a(), b(), c())",
    ),
    Case(
        "test_a_function_that_is_the_block_plus_its_return_shares_the_helper",
        "v = 5\nw = 5\n\ndef compute():\n    tmp = v + 1\n    base = tmp - 3\n    total = base * 2\n    return total\n\ndef show():\n    tmp = v + 1\n    base = tmp - 3\n    total = base * 2\n    return f'show {total}'\n\ndef label():\n    step = w + 1\n    offset = step - 3\n    amount = offset * 2\n    return f'label {amount}'",
        "v = 3\nw = 11\nprint(compute(), show(), label())",
    ),
    Case(
        "test_a_function_returning_the_names_in_another_order_keeps_its_order",
        "v = 5\nw = 5\n\ndef compute():\n    lo = v - 1\n    hi = v + 1\n    mid = (lo + hi) / 2\n    return (lo, hi)\n\ndef show():\n    lo = v - 1\n    hi = v + 1\n    mid = (lo + hi) / 2\n    return f'show {lo} {hi}'\n\ndef label():\n    lo = v - 1\n    hi = v + 1\n    mid = (lo + hi) / 2\n    return f'label {lo} {hi}'",
        "v = 3\nw = 11\nprint(compute(), show(), label())",
    ),
    Case(
        "test_unused_thunk_inputs_disappear_without_changing_effects",
        "\nclass Config:\n    def __getattribute__(self, name):\n        if name in ('first', 'second'):\n            print('read', name)\n            return 5\n        return object.__getattribute__(self, name)\ndef first(config, value):\n    print('before', value)\n    result = config.first + value\n    print('after', result)\n    return result\ndef second(other, value):\n    print('before', value)\n    result = other.second + value\n    print('after', result)\n    return result\nprint(first(Config(), 2), second(Config(), 4))\n",
        "",
    ),
    Case(
        "test_inferred_local_callable_keeps_argument_and_result_correlation",
        '\nfrom typing import Callable, TypeVar\nT = TypeVar("T")\nU = TypeVar("U")\ndef first(items: list[T], functions: list[Callable[[T], T]]) -> T:\n    callback = functions[0]\n    head = items[0]\n    assert len(items) > 0\n    result = callback(head)\n    wrapped = [result]\n    return wrapped[0]\ndef second(items: list[U], functions: list[Callable[[U], U]]) -> U:\n    callback = next(iter(functions))\n    head = next(iter(items))\n    if not items:\n        raise ValueError("empty")\n    result = callback(head)\n    wrapped = [result]\n    return wrapped[0]\n',
        'print(first([3], [lambda value: value * 2]), second(["word"], [str.upper]))',
    ),
    Case(
        "test_constrained_local_specializations_are_not_silently_narrowed",
        '\nfrom typing import TypeVar\nT = TypeVar("T", int, str)\nU = TypeVar("U", int, str)\ndef first(items: list[T]) -> T:\n    head = items[0]\n    assert len(items) > 0\n    result = [head]\n    return result[0]\ndef second(items: list[U]) -> U:\n    head = next(iter(items))\n    if not items:\n        raise ValueError("empty")\n    result = [head]\n    return result[0]\n',
        'print(first([3]), second(["word"]), first(["a"]), second([7]))',
    ),
    Case(
        "test_declared_return_types_need_a_checker_to_meet",
        "\ndef first(value: int) -> int:\n    total = value * 2\n    text = str(total)\n    return len(text.strip())\n\ndef second(value: int) -> int | None:\n    total = value * 2\n    text = str(total)\n    return len(text.strip())\n",
        "print(first(123), second(-456))",
    ),
    Case(
        "test_unrelated_declared_return_types_leave_the_return_to_any",
        "\ndef first(value: int) -> int:\n    total = value * 2\n    text = str(total)\n    return len(text.strip())\n\ndef second(value: int) -> str:\n    total = value * 2\n    text = str(total)\n    return len(text.strip())\n",
        "print(first(123), second(-456))",
    ),
    Case(
        "test_declared_return_type_is_used_when_every_site_returns_the_call",
        "\ndef first(value: int) -> str:\n    total = value * 2\n    text = str(total)\n    return text.strip()\n\ndef second(value: int) -> str:\n    total = value * 2\n    text = str(total)\n    return text.strip()\n",
        "print(first(123), second(-456))",
    ),
    Case(
        "test_the_type_the_block_saw_replaces_a_wider_declared_one",
        '\nclass Text:\n    def __init__(self, plain: str, style: str = "") -> None:\n        self.plain = plain\n        self.style = style\n\n    def __len__(self) -> int:\n        return len(self.plain)\n\n\nclass Buffer:\n    def __init__(self) -> None:\n        self._spans: list[tuple[int, int, str]] = []\n        self._text: list[str] = []\n        self._length = 0\n\n    def append(self, text: Text | str) -> None:\n        if isinstance(text, str):\n            self._text.append(text)\n            return\n        length = self._length\n        if text.style:\n            self._spans.append((length, length + len(text), text.style))\n        self._text.append(text.plain)\n\n    def append_text(self, text: Text) -> None:\n        length = self._length\n        if text.style:\n            self._spans.append((length, length + len(text), text.style))\n        self._text.append(text.plain)\n',
        'b = Buffer()\nb.append("plain")\nb.append(Text("styled", "red"))\nb.append_text(Text("more", "blue"))\nprint(b._text, b._spans, b._length)',
    ),
    Case(
        "test_partial_generic_function_bodies_keep_type_parameter_bindings",
        "\ndef first[T](x):\n    assert x\n    a = T.__name__\n    b = a + str(x)\n    c = b.upper()\n    return c\n\ndef second[T](x):\n    if x < 0:\n        raise ValueError(x)\n    a = T.__name__\n    b = a + str(x)\n    c = b.upper()\n    return c\n\nprint(first(1), second(2))\n",
        "print(first(1), second(2))",
    ),
)
