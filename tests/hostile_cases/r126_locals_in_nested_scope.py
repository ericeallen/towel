def lo1(a):
    b = a + 1
    def inner():
        c = b * 2
        d = c + 1
        return sorted(locals()), c, d
    r = inner()
    return r, b
def lo2(a):
    b = a + 1
    def inner():
        c = b * 2
        d = c + 1
        return sorted(locals()), c, d
    r = inner()
    return r, b, 2
def lo3(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(vars())
    return names, d
def lo4(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(vars())
    return names, d, 4
if __name__ == "__main__":
    print(lo1(1), lo2(2), lo3(1), lo4(2))
