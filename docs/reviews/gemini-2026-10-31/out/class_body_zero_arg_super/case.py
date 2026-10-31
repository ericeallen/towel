class Base:
    def step(self, x):
        return x + 100


class Derived(Base):
    def f1(self, x):
        a = x + 1
        b = super().step(a)
        c = b * 2
        return c + 1

    def f2(self, x):
        a = x + 1
        b = super().step(a)
        c = b * 2
        return c + 2
