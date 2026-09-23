# The star import binds ``len`` to util's function, a binding no scope
# analysis of this module lists; a helper in a.py reading ``len`` bare
# would reach the builtin instead.
from pkg.util import *


def fb(values):
    print("pre", "b")
    size = len(values) if hasattr(values, "__len__") else values
    size = size * 2
    print("post", size)
    return size
