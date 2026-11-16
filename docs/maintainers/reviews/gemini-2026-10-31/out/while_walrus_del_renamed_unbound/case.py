def __extracted_func_0(__param_0, x):
    while (alpha := x) > 0:
        if x == 5:
            del alpha
            break
    return alpha + __param_0


def f1(x):
    return __extracted_func_0(10, x)


def f2(x):
    return __extracted_func_0(20, x)
