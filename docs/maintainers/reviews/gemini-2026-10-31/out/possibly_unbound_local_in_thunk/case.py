def f1(x, flag):
    if x > 0:
        maybe = x * 10
    a = x + 1
    b = a * 2
    if flag:
        b += maybe + x
    return b


def f2(x, flag):
    a = x + 1
    b = a * 2
    if flag:
        b += (x * 3) + x
    return b
