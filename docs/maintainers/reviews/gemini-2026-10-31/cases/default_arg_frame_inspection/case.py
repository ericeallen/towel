def f1(x):
    step = x + 1
    scaled = step * 2
    result = scaled + 3
    def capture(snapshot=sorted(dir())):
        return snapshot
    return result, capture()


def f2(x):
    step = x + 1
    scaled = step * 2
    result = scaled + 3
    return result * 10
