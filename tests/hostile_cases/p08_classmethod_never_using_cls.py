# A classmethod that never reads its class works when its function is called
# with anything in the class's place.
class C:
    @classmethod
    def a(cls, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
    @classmethod
    def b(cls, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
if __name__ == "__main__":
    print(C.a([1, 5, 9]), C.__dict__['b'].__func__(None, [1, 5, 9]))
