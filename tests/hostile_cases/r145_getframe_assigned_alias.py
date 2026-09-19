# ``gf = s._getframe`` is a frame read under another name; every block that
# calls it is declined, like the direct spellings below.
import sys as s
import inspect
gf = s._getframe
def fr1(a):
    b = a + 1
    c = b * 2
    name = gf().f_code.co_name
    return name, b, c
def fr2(a):
    b = a + 1
    c = b * 2
    name = gf().f_code.co_name
    return name, b, c, 2
def fr3(a):
    b = a + 1
    c = b * 2
    name = s._getframe(0).f_code.co_name
    return name, b, c
def fr4(a):
    b = a + 1
    c = b * 2
    name = s._getframe(0).f_code.co_name
    return name, b, c, 4
def fr5(a):
    b = a + 1
    c = b * 2
    name = inspect.currentframe().f_code.co_name
    return name, b, c
def fr6(a):
    b = a + 1
    c = b * 2
    name = inspect.currentframe().f_code.co_name
    return name, b, c, 6
def fr7(a):
    b = a + 1
    c = b * 2
    name = getattr(s, "_getframe")().f_code.co_name
    return name, b, c
def fr8(a):
    b = a + 1
    c = b * 2
    name = getattr(s, "_getframe")().f_code.co_name
    return name, b, c, 8
if __name__ == "__main__":
    print(fr1(1), fr2(2), fr3(3), fr4(4), fr5(5), fr6(6), fr7(7), fr8(8))
