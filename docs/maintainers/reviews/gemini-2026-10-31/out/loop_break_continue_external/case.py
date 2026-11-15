def f1(items):
    total = 0
    for item in items:
        step = item + 1
        scaled = step * 2
        if scaled > 10:
            break
        total += scaled + 1
    return total


def f2(items):
    total = 0
    for item in items:
        step = item + 1
        scaled = step * 2
        if scaled > 10:
            break
        total += scaled + 2
    return total
