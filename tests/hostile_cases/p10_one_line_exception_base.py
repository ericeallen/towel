# The common ancestor's body shares its header's line, so a helper appended to
# the body cannot be indented under it.
class AppError(Exception): k = 0
class A(AppError):
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
class B(AppError):
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
