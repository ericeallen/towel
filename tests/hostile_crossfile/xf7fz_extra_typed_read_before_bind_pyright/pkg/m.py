def scale(x: int) -> int:
    return x * 2


def f1(xs: list[int]) -> int:
    print("start", len(xs))
    scale = scale(len(xs))
    print("after", scale)
    return scale


def f2(xs: list[int]) -> int:
    print("begin", len(xs) * 2)
    scale = scale(len(xs))
    print("after", scale)
    return scale
