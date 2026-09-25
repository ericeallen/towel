from . import a


def fb(xs):
    total = 0
    for x in xs:
        total += x * 3
    print("fb", total)
    return total + 1
