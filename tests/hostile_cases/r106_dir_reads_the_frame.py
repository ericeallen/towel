def f(a, b):
    unused = 1
    total = a + b
    names = sorted(dir())
    count = len(names) + total
    return count, names
def g(a, b):
    total = a + b
    names = sorted(dir())
    count = len(names) + total
    return count * 2, names
if __name__ == "__main__":
    print(f(1, 2), g(1, 2))
