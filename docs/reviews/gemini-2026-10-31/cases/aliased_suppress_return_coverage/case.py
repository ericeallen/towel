from contextlib import suppress as ignore_errors


def f1(x):
    if x > 0:
        with ignore_errors(ValueError):
            if x == 10:
                return 100
            raise ValueError("skip")
    else:
        return -1
    return x + 1


def f2(x):
    if x > 0:
        with ignore_errors(ValueError):
            if x == 10:
                return 200
            raise ValueError("skip")
    else:
        return -2
    extra = x * 3
    return extra
