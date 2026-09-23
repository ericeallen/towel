# The decorator builds a new class from the names the class declares, so a
# helper appended to the body is dropped.
def keep_declared(cls):
    ns = {name: getattr(cls, name) for name in cls.__keep__}
    return type(cls.__name__, cls.__bases__, ns)

@keep_declared
class A:
    __keep__ = ("a", "b", "k")
    k = 0
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
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
    print(A().a([1, 5, 9]), A().b([1, 5, 9]))
