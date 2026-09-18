from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from decimal import Decimal as T_A
    from fractions import Fraction as T_B
def f(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(T_A)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(T_B)
    total = sum(acc) + len(acc)
    return total * 2
if __name__ == "__main__":
    print(f(False, [1, 2]), g(False, [3]))
