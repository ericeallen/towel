from . import a


def fc(xs):
    total = 0
    for x in xs:
        total += x * 3
    print("fc", total)
    return total + 1
