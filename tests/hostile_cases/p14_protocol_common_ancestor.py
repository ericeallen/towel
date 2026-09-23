# Neither subclass is a protocol, but their common ancestor is: the helper must
# not become one of its members.
import typing

class Base(typing.Protocol):
    k: int

class A(Base):
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
class B(Base):
    k = 0
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
    print(sorted(getattr(Base, "__protocol_attrs__", ())))
