from contextlib import suppress as ignore_errors


def f1(x, use_saved):
    with ignore_errors(ValueError):
        if x < 0:
            raise ValueError("negative")
        saved = x * 10
    a = x + 1
    b = a * 2
    if use_saved:
        b += saved
    return b


def f2(x, use_saved):
    fallback = 99
    a = x + 1
    b = a * 2
    if use_saved:
        b += fallback
    return b
