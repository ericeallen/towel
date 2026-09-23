# The helper's annotations name Late, so it may be placed after Late to spell
# them bare; but the assignment before Late already calls f, which needs it.
from __future__ import annotations
from typing import Optional


def f(x: Optional[Late], items: list[int]) -> int:
    total = 0
    for item in items:
        if item > 4:
            total += item * 2
        else:
            total -= item
    result = total + (0 if x is None else x.k)
    return result * 3


def g(x: Optional[Late], items: list[int]) -> int:
    total = 0
    for item in items:
        if item > 5:
            total += item * 2
        else:
            total -= item
    result = total + (0 if x is None else x.k)
    return result * 3


Y = f(None, [1, 5, 9])


class Late:
    k = 1


if __name__ == "__main__":
    print(Y, g(Late(), [1, 5, 9]))
