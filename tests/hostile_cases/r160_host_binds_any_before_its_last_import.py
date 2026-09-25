# The module binds ``Any`` itself, in a ``try`` that is no import statement,
# before its last leading import. The helper's bare thunk parameter is
# annotated ``Any``; a ``from typing import Any`` written after that import
# would replace the module's own matcher, and ``Any("x")`` would raise.
try:
    from a_matcher_library_that_is_not_installed import Any
except ImportError:
    class Any:
        def __init__(self, label="any"):
            self.label = label

import os


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
    print(f1([1, 2], Config()), f2([3], Config()))
    print(Any("x").label, os.sep == os.path.sep)
