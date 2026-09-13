class D:
    def __init__(self, func): self.func = func
def g1(obj, self):
    if obj is None:
        return self
    cls = obj.__class__
    name = cls.__name__
    return name.upper() + "1"
def g2(obj, self):
    if obj is None:
        return self
    cls = obj.__class__
    name = cls.__name__
    print(name)
    return name
if __name__ == "__main__":
    print(g1(None, "s"), g1(D(1), "s"), g2(None, "t"), g2([], "t"))
