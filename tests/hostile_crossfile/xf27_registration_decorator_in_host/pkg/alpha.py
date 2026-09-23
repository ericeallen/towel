# Importing this module registers ``plugin``: a helper hosted here would
# make importing beta register it too.
from pkg.registry import register


@register
def plugin():
    return 1


def summarize(values):
    print("summarize")
    total = 0
    for value in values:
        total += value * 2
    return total + 1
