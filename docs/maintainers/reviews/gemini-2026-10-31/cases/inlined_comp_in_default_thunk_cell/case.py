def outer(items, flag):
    k = 100

    def f1(x):
        def helper_with_default(cached=[k * 2 for k in items]):
            return sum(cached)
        total = helper_with_default()
        a = x + 1
        b = a * 2
        if flag:
            b += k + x
        return total + b

    def f2(x):
        total = len(items)
        a = x + 1
        b = a * 2
        if flag:
            b += (x + 1) + x
        return total + b

    return f1(5), f2(5)
