from collections.abc import Callable


class Any:
    def __init__(self, label: str = "any") -> None:
        self.label = label


from pkg.a import *  # noqa: E402,F403


def kind(value: object) -> str:
    match value:
        case Callable():
            return "callable"
        case _:
            return "other"


def label() -> str:
    return Any("b").label
