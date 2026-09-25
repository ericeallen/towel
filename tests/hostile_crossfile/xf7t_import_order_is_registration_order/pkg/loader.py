# gamma must load before beta, which ruff would sort first.
from pkg import gamma  # noqa: F401
from pkg import beta  # noqa: F401
from pkg.registry import PLUGINS


def load(names):
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name.upper())
    print("load", seen, PLUGINS[-1])
    return seen


def load_again(names):
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name.upper())
    print("load", seen, PLUGINS[-1])
    return seen
