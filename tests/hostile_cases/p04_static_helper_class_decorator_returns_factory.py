# The decorator binds the class's name to a factory, not to the class.
def factory(cls):
    def make():
        return cls()
    return make
@factory
class C:
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
    def b(self, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
if __name__ == "__main__":
    c = C()
    print(c.a([1, 5, 9]), c.b([1, 5, 9]))
