# As r163, but the methods read self, so a helper would be a method of A and
# keep the mangling. The call still raises, but its TypeError names the lambda
# by its qualified name, which would become A.__extracted_func_0.<locals>.
class A:
    def __init__(self):
        self.k = 1

    def m1(self, x):
        y = x + self.k
        z = (lambda __p=0: 7)(__p=y)
        print("m1", y, z)
        return z

    def m2(self, x):
        y = x + self.k
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
