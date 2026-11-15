def outer(items, flag):
    k = 50

    def f1(x):
        doubled = [k * 2 for k in items]
        a = x + 1
        b = a * 2
        if flag:
            b += k + x
        return sum(doubled) + b

    def f2(x):
        total = len(items)
        a = x + 1
        b = a * 2
        if flag:
            b += (x + 1) + x
        return total + b

    return f1(5), f2(5)
