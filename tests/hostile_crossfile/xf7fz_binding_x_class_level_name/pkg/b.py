from . import a


class Item:
    kind = "b"


def fb(ys):
    it = Item()
    t = len(ys)
    print("fb", t, it.kind, type(it).__module__)
    return it.kind


def use():
    return a.fa([1])
