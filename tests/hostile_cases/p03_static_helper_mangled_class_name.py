# Inside a class body a name like __C is mangled, so the class cannot spell
# its own name.
class __C:
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
C = __C
if __name__ == "__main__":
    print(C().a([1, 5, 9]), C().b([1, 5, 9]))
