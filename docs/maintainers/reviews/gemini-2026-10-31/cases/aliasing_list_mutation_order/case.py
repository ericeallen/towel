def f1(x):
    items = [x, x + 1]
    alias = items
    alias.append(x + 2)
    total = sum(items) + items[0]
    return total


def f2(x):
    items = [x, x + 1]
    alias = items
    alias.append(x + 2)
    total = sum(items) + items[-1]
    return total
