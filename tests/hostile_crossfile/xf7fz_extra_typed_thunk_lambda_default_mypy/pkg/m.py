from typing import Callable


class Loud:
    def __init__(self, v: int) -> None:
        self.v = v

    def alpha(self) -> int:
        print("alpha")
        return self.v

    def beta(self) -> int:
        print("beta")
        return self.v * 2


def tr(tag: str, v: int) -> int:
    print("tr", tag)
    return v


def f1(o: Loud) -> int:
    g: Callable[[], int] = lambda k=tr("default", 1): k
    y = o.alpha()
    z = g() + y
    print("f1", y, z)
    return z


def f2(o: Loud) -> int:
    g: Callable[[], int] = lambda k=tr("default", 1): k
    y = o.beta() or 0
    z = g() + y
    print("f2", y, z)
    return z
