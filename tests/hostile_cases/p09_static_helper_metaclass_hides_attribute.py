# A metaclass that intercepts class attribute lookup sees a helper reached
# through the class, where the original methods made no such lookup.
class Meta(type):
    def __getattribute__(cls, name):
        if name.startswith("_extracted"):
            raise AttributeError(name)
        return super().__getattribute__(name)
class C(metaclass=Meta):
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
    print(C().a([1, 5, 9]), C().b([1, 5, 9]))
