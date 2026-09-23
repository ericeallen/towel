# Only this module rebinds ``len``. Whichever pass pairs this block with
# the others, or with the helper they share, must pass ``len`` in.
from pkg.util import len


def fc(values):
    print("pre", "c")
    size = len(values) if hasattr(values, "__len__") else values
    size = size * 2
    print("post", size)
    return size
