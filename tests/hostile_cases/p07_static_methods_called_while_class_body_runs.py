class C:
    @staticmethod
    def a(items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
    @staticmethod
    def b(items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
    A0 = a([1, 5, 9])
    B0 = b([1, 5, 9])
if __name__ == "__main__":
    print(C.A0, C.B0)
