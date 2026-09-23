# ``summarize`` is the whole of what ``aggregate`` in beta.py does, but
# importing this module registers ``plugin``.
from pkg.registry import register


@register
def plugin():
    return 1


def summarize(values):
    total = 0
    for value in values:
        total += value * 2
    return total + 1
