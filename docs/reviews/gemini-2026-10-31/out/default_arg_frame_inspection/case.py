def __extracted_func_0(x):
    step = x + 1
    scaled = step * 2
    result = scaled + 3
    return result


def f1(x):
    result = __extracted_func_0(x)
    def capture(snapshot=sorted(dir())):
        return snapshot
    return result, capture()


def f2(x):
    result = __extracted_func_0(x)
    return result * 10
