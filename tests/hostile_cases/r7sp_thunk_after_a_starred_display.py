# ``[*x]`` iterates x, which runs __iter__, before the thunk.
class Loud:
    def __init__(self, v): self.v = v
    def alpha(self):
        print("alpha called"); return self.v
    def beta(self):
        print("beta called"); return self.v * 2
    def __iter__(self):
        print("iterating"); return iter([self.v])
def f1(x, o):
    items = [*x]
    y = o.alpha()
    z = y + 1
    print("f1", y, z, items)
    return z
def f2(x, o):
    items = [*x]
    y = o.beta() or 0
    z = y + 1
    print("f2", y, z, items)
    return z
if __name__ == "__main__":
    print(f1(Loud(7), Loud(3)), f2(Loud(7), Loud(3)))
