# A method of a Protocol class is a protocol member: a helper placed there is
# one more member every structural implementer lacks.
from typing import Protocol, runtime_checkable

@runtime_checkable
class P(Protocol):
    k: int
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
class Impl:
    k = 1
    def a(self, items):
        return 1
    def b(self, items):
        return 2
class Explicit(P):
    k = 2
if __name__ == "__main__":
    print(isinstance(Impl(), P), Explicit().a([1, 5, 9]), Explicit().b([1, 5, 9]))
    print(sorted(getattr(P, "__protocol_attrs__", ())))
