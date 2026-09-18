e = eval
x_ = exec
import builtins as bi
def ev1(a, b):
    total = a + b
    doubled = total * 2
    r = e("total + doubled")
    s = str(r) + "x"
    return r, s
def ev2(a, b):
    total = a + b
    doubled = total * 2
    r = e("total + doubled")
    s = repr(r)
    return r, s, 2
def bv1(a, b):
    total = a + b
    doubled = total * 2
    r = bi.eval("total + doubled")
    s = str(r) + "x"
    return r, s
def bv2(a, b):
    total = a + b
    doubled = total * 2
    r = bi.eval("total + doubled")
    s = repr(r)
    return r, s, 2
def lv1(a, b):
    total = a + b
    doubled = total * 2
    r = sorted(bi.locals())
    s = str(r) + "x"
    return r, s
def lv2(a, b):
    total = a + b
    doubled = total * 2
    r = sorted(bi.locals())
    s = repr(r)
    return r, s, 2
def xv1(a, b):
    total = a + b
    doubled = total * 2
    x_("total = doubled + 1")
    s = str(total) + "x"
    return total, doubled, s
def xv2(a, b):
    total = a + b
    doubled = total * 2
    x_("total = doubled + 1")
    s = repr(total)
    return total, doubled, s, 2
if __name__ == "__main__":
    print(ev1(1, 2), ev2(3, 4), bv1(1, 2), bv2(3, 4), lv1(1, 2), lv2(3, 4), xv1(1, 2), xv2(3, 4))
