def ev1(a, b):
    k = a * 100
    total = a + b
    doubled = total * 2
    r = eval("total + doubled + k")
    s = str(r) + "x"
    return r, s
def ev2(a, b):
    k = a * 100
    total = a + b
    doubled = total * 2
    r = eval("total + doubled + k")
    s = repr(r)
    return r, s, 2
if __name__ == "__main__":
    print(ev1(1, 2), ev2(3, 4))
