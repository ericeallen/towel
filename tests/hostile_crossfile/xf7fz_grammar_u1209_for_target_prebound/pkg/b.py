from . import a
from .common import *


def f2(a, b, c, o):
    k1 = 3
    def inner():
        print("cell", k1)
        v1 = 6
        v2 = (v3 := c.get('k', 0)) + 1
        v4 = 0
        while v4 < 3:
            o.v = ((v1 and c.get('k', 0)) or max(a, v2))
            v4 += 1
        for v1 in b:
            print("it", v1)
        print("last", v1)
        print("after", v3, v4)
        return None
    return inner()
