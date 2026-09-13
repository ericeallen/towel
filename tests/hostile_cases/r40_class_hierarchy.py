class Base:
    def __init__(self, x, y): self.x = x; self.y = y
    @property
    def noisy(self):
        print("noisy"); return self.x
class A(Base):
    def run(self):
        if not self.noisy:
            return "empty"
        if len(self.noisy) < 2:
            return "short"
        return self.noisy.upper()
class B(Base):
    def run(self):
        if not self.y:
            return "empty"
        if len(self.y) < 2:
            return "short"
        return self.y.upper()
if __name__ == "__main__":
    print(A("ab", "cd").run(), B("", "e").run(), A("", "zz").run())
