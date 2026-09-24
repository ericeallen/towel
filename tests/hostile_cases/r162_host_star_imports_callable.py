# ``Callable`` is collections.abc's, bound by a star import no list of the
# module's imports names. The copied annotation ``Callable[[], int]`` needs
# no import at all; ``from typing import Callable`` after the star import
# would make ``case Callable():`` raise "called match pattern must be a class".
from collections.abc import *


def first(make: Callable[[], int], label: str) -> int:
    value = make()
    text = f"{label}:{value}"
    print(text)
    return value + 1


def second(make: Callable[[], int], label: str) -> int:
    value = make()
    text = f"{label}:{value}"
    print(text)
    return value + 1


def kind(value: object) -> str:
    match value:
        case Callable():
            return "callable"
        case _:
            return "other"


if __name__ == "__main__":
    print(first(lambda: 1, "a"), second(lambda: 2, "b"))
    print(kind(len), kind(3))
