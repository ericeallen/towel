def f1(x):
    while (alpha := x) > 0:
        if x == 5:
            del alpha
            break
    return alpha + 10


def f2(x):
    while (beta := x) > 0:
        if x == 5:
            del beta
            break
    return beta + 20
