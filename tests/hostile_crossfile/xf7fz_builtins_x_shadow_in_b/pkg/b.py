from . import a


def len(v):
    return 42


def fb(xs):
    pass
    n = len(xs)
    total = 0
    for x in xs:
        total += x * n
    print("fb", n, total)
    return total
