def lo1(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(locals())
    return names, d
def lo2(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(locals())
    return names, d, 2
def di1(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(dir())
    return names, d
def di2(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(dir())
    return names, d, 2
def fl1(a):
    b = a + 1
    c = b * 2
    d = c + 1
    import sys
    names = sorted(sys._getframe().f_locals)
    return names, d
def fl2(a):
    b = a + 1
    c = b * 2
    d = c + 1
    import sys
    names = sorted(sys._getframe().f_locals)
    return names, d, 2
if __name__ == "__main__":
    print(lo1(1), lo2(2), di1(1), di2(2), fl1(1), fl2(2))
