# A method that never reads its receiver shares a block with its twin. The
# helper must not be reached through the class's name: inside these methods
# that name is a parameter.
class C:
    def a(self, items, C=None):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
    def b(self, items, C=None):
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
