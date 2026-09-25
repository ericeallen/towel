from typing import Any

LOG: list[str] = []
G = 5


def tr(tag: str, v: Any) -> Any:
    print("tr", tag, repr(v))
    LOG.append(tag)
    return v


class Box:
    def __init__(self, v: int) -> None:
        self.v = v

    def __repr__(self) -> str:
        return f"Box({self.v!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Box) and self.v == other.v


class Ctx:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> str:
        print("enter", self.tag)
        return self.tag

    def __exit__(self, et: Any, ev: Any, tb: Any) -> bool:
        print("exit", self.tag, et.__name__ if et else None)
        return False


def bump() -> int:
    global G
    G += 1
    return G


class K:
    def __init__(self) -> None:
        self.count = 0

    def f1(self, a: int, b: list[int], c: dict[str, int], o: Box) -> Any:
        print("prefix", (bump() * (a + c.get('k', 0))))
        print("p", abs((1 if len(b) < 9 <= c.get('k', 0) else 0))); print("p", abs(sum(q for q in b if q > c.get('k', 0))))
        v1 = 0
        while v1 < 3:
            c['j'] = (sum(b) - (lambda q: q * 2 + 1)(len(b)))
            v1 += 1
        if (v1 + G) != 0:
            v1 -= G
            assert not (v1 + v1), "assert failed " + str((lambda q: q * 2 + 1)(G))
        v2 = (v3 := ((G or v1) and sum(q for q in b if q > len(b)))) + 1
        print("after", v2)
        return None

    def f2(self, a: int, b: list[int], c: dict[str, int], o: Box) -> Any:
        print("site1-prefix", a)
        print("prefix", (bump() * (a + c.get('k', 0))))
        print("p", (abs(len(b)) and o.v)); print("p", abs(sum(q for q in b if q > c.get('k', 0))))
        v1_1 = 0
        while v1_1 < 3:
            c['j'] = (sum(b) - (lambda q: q * 2 + 1)(len(b)))
            v1_1 += 1
        if (v1_1 + G) != 0:
            v1_1 -= G
            assert not (v1_1 + v1_1), "assert failed " + str((lambda q: q * 2 + 1)(G))
        v2_1 = (v3_1 := ((G or v1_1) and sum(q for q in b if q > len(b)))) + 1
        print("after", v2_1)
        return None

    def f3(self, a: int, b: list[int], c: dict[str, int], o: Box) -> Any:
        print("prefix", (bump() * (a + c.get('k', 0))))
        print("p", (abs(len(b)) and o.v)); print("p", abs(sum(q for q in b if q > c.get('k', 0))))
        v1 = 0
        while v1 < 3:
            c['j'] = (sum(b) - (lambda q: q * 2 + 1)(len(b)))
            v1 += 1
        if (v1 + G) != 0:
            v1 -= G
            assert not (v1 + v1), "assert failed " + str((lambda q: q * 2 + 1)(G))
        v2 = (v3 := ((G or v1) and sum(q for q in b if q > len(b)))) + 1
        print("after", v2)
        return None
