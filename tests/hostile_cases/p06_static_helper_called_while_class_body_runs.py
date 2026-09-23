# The class body calls its methods before the class exists; a helper appended
# to the body, or reached through the class, is not there yet.
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
    A0 = a(None, [1, 5, 9])
if __name__ == "__main__":
    print(C.A0, C().b([1, 5, 9]))
