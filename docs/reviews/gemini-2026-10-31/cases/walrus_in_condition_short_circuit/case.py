def f1(x, flag):
    a = x + 1
    b = a * 2
    if flag and (captured := b + 10) > 0:
        return captured + 1
    return b


def f2(x, flag):
    a = x + 1
    b = a * 2
    if flag and (captured := b + 20) > 0:
        return captured + 2
    return b * 3
