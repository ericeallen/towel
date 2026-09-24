# The plugins must load in this order: zeta overrides alpha.
from pkg import zeta  # noqa: F401
from pkg import alpha  # noqa: F401
from pkg.registry import PLUGINS


def summarize(rows):
    total = 0
    for row in rows:
        if row > 0:
            total += row * 2
    print("summarize", total, PLUGINS[0])
    return total


def summarize_again(rows):
    total = 0
    for row in rows:
        if row > 0:
            total += row * 2
    print("summarize", total, PLUGINS[0])
    return total
