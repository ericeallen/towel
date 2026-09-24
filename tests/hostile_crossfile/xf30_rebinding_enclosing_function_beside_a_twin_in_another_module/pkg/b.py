# cb rebinds the x that f and g read after calling it; the benign twin in a.py
# once lent them its memoized verdict that nothing does (round-3 audit, P1-1).
def hazard():
    x = 0

    def cb():
        nonlocal x
        x += 1

    def f():
        print("start", x)
        cb()
        print("after", x)
        print("end")

    def g():
        print("start", x)
        cb()
        print("after", x)
        print("end")

    f()
    g()
