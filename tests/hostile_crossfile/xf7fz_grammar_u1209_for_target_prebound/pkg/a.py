from .common import *


def f1(a, b, c, o):
    k0 = 2
    def inner():
        print("cell", k0)
        v1 = 6
        v2 = (v3 := (1 if sum(q for q in b if q > o.v) < (G if (G > 0 and G < 5) else o.v) <= (G or G) else 0)) + 1
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
