# The control for r163 and r164: without a private name the keyword binds the
# parameter wherever the code runs, and both pairs share a helper.
class A:
    def m1(self, x):
        y = x + 1
        z = (lambda p=0: 7)(p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + 1
        z = (lambda p=0: 7)(p=y)
        print("m2", y, z)
        return z


class B:
    def __init__(self):
        self.k = 1

    def m1(self, x):
        y = x + self.k
        z = (lambda p=0: 7)(p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + self.k
        z = (lambda p=0: 7)(p=y)
        print("m2", y, z)
        return z


if __name__ == "__main__":
    print(A().m1(1), A().m2(2), B().m1(3), B().m2(4))
