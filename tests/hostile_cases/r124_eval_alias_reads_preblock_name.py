e = eval
import builtins as bi
def ev1(a, b):
    k = a * 100
    total = a + b
    doubled = total * 2
    r = bi.eval("total + doubled + k")
    s = str(r) + "x"
    return r, s
def ev2(a, b):
    k = a * 100
    total = a + b
    doubled = total * 2
    r = bi.eval("total + doubled + k")
    s = repr(r)
    return r, s, 2
def ex1(a, b):
    k = a * 100
    total = a + b
    doubled = total * 2
    bi.exec("k = total + doubled")
    s = str(k) + "x"
    return k, s
def ex2(a, b):
    k = a * 100
    total = a + b
    doubled = total * 2
    bi.exec("k = total + doubled")
    s = repr(k)
    return k, s, 2
def gl1(a, b):
    total = a + b
    doubled = total * 2
    g = bi.globals()
    g["LAST"] = total
    s = str(doubled) + "x"
    return s
def gl2(a, b):
    total = a + b
    doubled = total * 2
    g = bi.globals()
    g["LAST"] = total
    s = repr(doubled)
    return s, 2
if __name__ == "__main__":
    print(ev1(1, 2), ev2(3, 4), ex1(1, 2), ex2(3, 4), gl1(1, 2), gl2(3, 4), globals().get("LAST"))
