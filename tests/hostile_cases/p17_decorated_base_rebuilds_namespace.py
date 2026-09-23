# The common ancestor is decorated, and the decorator keeps only public names.
def public_only(cls):
    ns = {k: v for k, v in vars(cls).items() if not k.startswith("_") or k.startswith("__")}
    ns.pop("__dict__", None)
    ns.pop("__weakref__", None)
    return type(cls.__name__, cls.__bases__, ns)

@public_only
class Base:
    k = 0
class A(Base):
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
class B(Base):
    def b(self, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
if __name__ == "__main__":
    print(A().a([1, 5, 9]), B().b([1, 5, 9]))
