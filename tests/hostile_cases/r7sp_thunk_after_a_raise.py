# Nothing after a raise runs: the thunk in the dead code after it is never
# evaluated, and the raise itself comes first.
class Loud:
    def __init__(self, v): self.v = v
    def alpha(self):
        print("alpha called"); return self.v
    def beta(self):
        print("beta called"); return self.v * 2
def f1(o, e):
    raise e
    y = o.alpha()
    print("f1", y)
    return y
def f2(o, e):
    raise e
    y = o.beta() or 0
    print("f2", y)
    return y
if __name__ == "__main__":
    for f in (f1, f2):
        try:
            f(Loud(3), ValueError("boom"))
        except ValueError as error:
            print("ValueError", error)
