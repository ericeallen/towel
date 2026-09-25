def f1(xs: list[int], flag: bool) -> int:
    item = xs[0]
    if flag:
        del item
    r = item * 2
    print("f1", r)
    return r


def f2(ys: list[int], flag: bool) -> int:
    elem = ys[0]
    if flag:
        del elem
    r = elem * 2
    print("f2", r)
    return r
