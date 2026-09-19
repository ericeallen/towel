# ``__class__`` is the defining class's cell, not a module name: a method helper
# hosted in the base class must take it as a parameter, not read it bare.
class Base:
    def describe(self):
        return "base"
    def build(self, n):
        return ["b"] * n
class C1(Base):
    def build(self, n):
        parts = super().build(n)
        parts.append(self.describe())
        parts.append(__class__.__name__)
        parts.append(len(parts))
        return parts
class C2(Base):
    def build(self, n):
        parts = super().build(n)
        parts.append(self.describe())
        parts.append(__class__.__name__)
        parts.append(len(parts))
        return parts
class C3(Base):
    def build(self, n):
        parts = super(C3, self).build(n)
        parts.append(self.describe())
        parts.append(type(self).__name__)
        parts.append(len(parts))
        return parts
    def describe(self):
        return "c3"
if __name__ == "__main__":
    print(C1().build(1), C2().build(2), C3().build(1))
