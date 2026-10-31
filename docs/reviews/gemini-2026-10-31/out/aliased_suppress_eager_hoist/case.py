from contextlib import suppress as ignore_errors


def __extracted_func_0(__param_0, use_saved, x):
    a = x + 1
    b = a * 2
    if use_saved:
        b += __param_0
    return b


def f1(x, use_saved):
    with ignore_errors(ValueError):
        if x < 0:
            raise ValueError("negative")
        saved = x * 10
    return __extracted_func_0(saved, use_saved, x)


def f2(x, use_saved):
    fallback = 99
    return __extracted_func_0(fallback, use_saved, x)
