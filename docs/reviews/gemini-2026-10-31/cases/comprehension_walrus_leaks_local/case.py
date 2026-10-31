def f1(items):
    a = len(items)
    vals = [(last := item * 2) for item in items]
    b = sum(vals) + a
    return b, last


def f2(items):
    a = len(items)
    vals = [(last := item * 3) for item in items]
    b = sum(vals) + a
    return b * 2, last
