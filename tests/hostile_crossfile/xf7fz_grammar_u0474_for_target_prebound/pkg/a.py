from .common import *


def f1(a, b, c, o):
    print("prefix", (lambda q: q * 2 + 1)((6 and a)))
    v1 = b[(sum(b) - o.v) % 4]
    v2 = bump()
    for v1 in b:
        print("it", v1)
    print("last", v1)
    print("after", v2, v1)
    return len(f"{v2}-{a}")
