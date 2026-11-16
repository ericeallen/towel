import weakref


class Box:
    pass


def f1(x):
    box = Box()
    ref = weakref.ref(box)
    scaled = x * 2
    return scaled + 1, ref() is not None


def f2(x):
    box = Box()
    ref = weakref.ref(box)
    scaled = x * 2
    return scaled + 2, ref() is not None
