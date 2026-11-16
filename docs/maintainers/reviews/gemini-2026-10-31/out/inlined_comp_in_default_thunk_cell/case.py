def outer(items, flag):

    def __extracted_func_0(__param_0, flag, total, x):
        a = x + 1
        b = a * 2
        if flag:
            b += __param_0() + x
        return total + b

    k = 100

    def f1(x):
        def helper_with_default(cached=[k * 2 for k in items]):
            return sum(cached)
        total = helper_with_default()
        return __extracted_func_0(lambda: k, flag, total, x)

    def f2(x):
        total = len(items)
        return __extracted_func_0(lambda: x + 1, flag, total, x)

    return f1(5), f2(5)
