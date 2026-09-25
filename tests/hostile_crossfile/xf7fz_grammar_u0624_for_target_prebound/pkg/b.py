from . import a
from .common import *


def f2(a, b, c, o):
    v1 = abs(c.get('k', 0))
    for v1 in b:
        print("it", v1)
    print("last", v1)
    match c.get('k', 0):
        case 0:
            v2, v3 = v1, sum(q for q in b if q > o.v)
        case 1 | 2:
            v4 = (sum(b) - (v1 + o.v))
        case _:
            v5 = (G // (max(v1, 0) % 5))
    for v1 in b:
        print("it", v1)
    print("last", v1)
    return None
