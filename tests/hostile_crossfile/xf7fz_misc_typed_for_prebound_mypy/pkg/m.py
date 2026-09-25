def f1(xs: list[int]) -> int:
    i = -1
    for i in xs:
        print("it", i)
    last = i * 2
    print("f1", last)
    return last


def f2(ys: list[int]) -> int:
    i = -2
    for i in ys:
        print("it", i)
    last = i * 2
    print("f2", last)
    return last
