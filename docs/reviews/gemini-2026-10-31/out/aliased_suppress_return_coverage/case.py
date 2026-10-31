from contextlib import suppress as ignore_errors


def __extracted_func_0(__param_0, __param_1, x):
    if x > 0:
        with ignore_errors(ValueError):
            if x == 10:
                return __param_0
            raise ValueError('skip')
    else:
        return -__param_1


def f1(x):
    return __extracted_func_0(100, 1, x)
    return x + 1


def f2(x):
    return __extracted_func_0(200, 2, x)
    extra = x * 3
    return extra
