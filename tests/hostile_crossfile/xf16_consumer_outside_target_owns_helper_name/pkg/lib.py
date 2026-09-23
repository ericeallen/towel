# The two methods share a block that reads the receiver, so the helper becomes
# a method of A. A subclass outside the package under refactoring already
# defines a method of the name Towel would pick first, and would override the
# helper for every call its instances make.
class A:
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
