def f1(x):
    err = 100
    try:
        a = x + 1
        if x < 0:
            raise ValueError("neg")
        b = a * 2
    except ValueError as err:
        b = -1
    return b, err


def f2(x):
    err = 200
    try:
        a = x + 1
        if x < 0:
            raise ValueError("neg")
        b = a * 2
    except ValueError as err:
        b = -2
    return b + 1, err
