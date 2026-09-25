# In a class body the lambda's parameter __p is stored as _A__p, and the
# keyword __p= is passed as written, so the call raises TypeError. Neither
# method reads self, so a helper would be a module-level function, where
# nothing is mangled and the call returns 7.
class A:
    def m1(self, x):
        y = x + 1
        z = (lambda __p=0: 7)(__p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + 1
        z = (lambda __p=0: 7)(__p=y)
        print("m2", y, z)
        return z


if __name__ == "__main__":
    subject = A()
    for method in (subject.m1, subject.m2):
        try:
            print(method(1))
        except TypeError as error:
            print("raised", error)
