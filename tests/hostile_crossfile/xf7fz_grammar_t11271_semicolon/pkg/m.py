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

    @classmethod
    def f1(cls, a: int, b: list[int], c: dict[str, int], o: Box) -> Any:
        print("cls", cls.__name__)
        print("prefix", ((lambda q: q * 2 + 1)(a) * bump()))
        while True:
            match G:
                case 0:
                    print("p", o.v)
                case 1 | 2:
                    print("p2", (G and (sum(b) - o.v)), tr('q5', (o.v or G)))
                case _:
                    o.v = a
            b.append(b[sum(q for q in b if q > a) % 4]); v1: int = c['k']
            c['j'] = (b[len(b) % 4] // ((sum(b) - G) % 5))
            return (1 if abs(c.get('k', 0)) < -2 <= v1 else 0)
            break

    @classmethod
    def f2(cls, x: int, y: list[int], z: dict[str, int], w: Box) -> Any:
        print("cls", cls.__name__)
        print("prefix", ((lambda q: q * 2 + 1)(x) * bump()))
        while True:
            match G:
                case 0:
                    print("p", w.v)
                case 1 | 2:
                    print("p2", (G and (sum(y) - w.v)), x)
                case _:
                    w.v = x
            y.append(y[sum(q for q in y if q > x) % 4]); v1: int = z['k']
            z['j'] = bump()
            return (1 if abs(z.get('k', 0)) < -2 <= v1 else 0)
            break

    @classmethod
    def f3(cls, a: int, b: list[int], c: dict[str, int], o: Box) -> Any:
        print("cls", cls.__name__)
        print("prefix", ((lambda q: q * 2 + 1)(a) * bump()))
        while True:
            match G:
                case 0:
                    print("p", o.v)
                case 1 | 2:
                    print("p2", (G and (sum(b) - o.v)), a)
                case _:
                    o.v = a
            b.append(b[sum(q for q in b if q > a) % 4]); v1_2: int = c['k']
            c['j'] = bump()
            return (1 if abs(c.get('k', 0)) < -2 <= v1_2 else 0)
            break
