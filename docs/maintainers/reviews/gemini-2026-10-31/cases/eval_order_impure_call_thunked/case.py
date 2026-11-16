def step(label, value):
    print(f"step:{label}:{value}")
    return value + 1


def f1(x):
    first = step("first", x)
    second = step("left", x * 2) + first
    third = second * 3
    return third


def f2(x):
    first = step("first", x)
    second = step("right", x + 5) + first
    third = second * 3
    return third
