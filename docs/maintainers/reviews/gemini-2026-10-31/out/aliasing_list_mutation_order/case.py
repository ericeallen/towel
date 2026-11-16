def __extracted_func_0(__param_0, x):
    items = [x, x + 1]
    alias = items
    alias.append(x + 2)
    total = sum(items) + items[__param_0]
    return total


def f1(x):
    return __extracted_func_0(0, x)


def f2(x):
    return __extracted_func_0(-1, x)
