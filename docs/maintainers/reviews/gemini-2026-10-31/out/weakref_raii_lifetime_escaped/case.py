import weakref


def __extracted_func_0(x):
    box = Box()
    ref = weakref.ref(box)
    scaled = x * 2
    return (box, ref, scaled)


class Box:
    pass


def f1(x):
    box, ref, scaled = __extracted_func_0(x)
    return scaled + 1, ref() is not None


def f2(x):
    box, ref, scaled = __extracted_func_0(x)
    return scaled + 2, ref() is not None
