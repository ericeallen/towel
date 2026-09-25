# Creating the lambda evaluates its default, an effect, before the thunk.
class Loud:
    def __init__(self, v): self.v = v
    def alpha(self):
        print("alpha called"); return self.v
    def beta(self):
        print("beta called"); return self.v * 2
def tr(tag, v):
    print("tr", tag); return v
def f1(x, o):
    g = lambda k=tr("lambda default", 1): k
    y = o.alpha()
    z = y + 1
    print("f1", y, z, g())
    return z
def f2(x, o):
    g = lambda k=tr("lambda default", 1): k
    y = o.beta() or 0
    z = y + 1
    print("f2", y, z, g())
    return z
if __name__ == "__main__":
    print(f1(0, Loud(3)), f2(0, Loud(3)))
