# Outside the package the run refactors: nothing in pkg imports it, and no
# scan of pkg's consumers need find it. Its own Any must survive the star import.
class Any:
    def __init__(self, label: str = "any") -> None:
        self.label = label


from pkg.report import *  # noqa: E402,F403


def describe() -> str:
    return f"{Any('consumer').label} {f1([4], Config())}"
