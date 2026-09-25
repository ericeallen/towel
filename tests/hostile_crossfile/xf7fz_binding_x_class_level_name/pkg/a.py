class Item:
    kind = "a"


def fa(xs):
    it = Item()
    t = len(xs)
    print("fa", t, it.kind, type(it).__module__)
    return it.kind
