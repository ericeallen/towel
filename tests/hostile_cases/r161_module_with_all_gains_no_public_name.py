# A module that states its exports in ``__all__``: a star import takes only
# those, but every other public name it binds is still an attribute every
# consumer reads, so an annotation's ``Any`` must not become one.
__all__ = ["Config", "f1", "f2"]


class Config:
    def __init__(self) -> None:
        self.scale = 2
        self.factor = 3


def f1(xs: list[int], cfg: Config) -> int:
    total = 0
    for x in xs:
        total += x * cfg.scale
        print("item", x)
    print("f1", total)
    return total


def f2(ys: list[int], cfg: Config) -> int:
    total = 0
    for y in ys:
        total += y * cfg.factor
        print("item", y)
    print("f2", total)
    return total


if __name__ == "__main__":
    import sys

    module = sys.modules[__name__]
    print(f1([1, 2], Config()), f2([3], Config()))
    print(sorted(name for name in dir(module) if not name.startswith("_")))
