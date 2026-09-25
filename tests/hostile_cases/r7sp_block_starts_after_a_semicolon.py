# Each block starts at the second statement of a line: the call must keep
# the first one, which binds the name the call passes.
def f1(n):
    a = n + 1; b = a * 2
    c = a + b
    print("f1", a, b, c)
    return c
def f2(n):
    a = n + 2; b = a * 2
    c = a + b
    print("f2", a, b, c)
    return c
def f3(n):
    a = n + 3; b = a * 2
    c = a + b
    print("f3", a, b, c)
    return c
if __name__ == "__main__":
    print(f1(1), f2(1), f3(1))
