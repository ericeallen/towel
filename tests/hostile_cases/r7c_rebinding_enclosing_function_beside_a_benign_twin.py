# The nested f and g of benign and of hazard are the same code, but hazard's
# cb rebinds the x they read after calling it: passed to a helper, x would be
# read before cb runs. A verdict that nothing rebinds x, memoized for the
# benign pair, once answered for the hazardous one too (round-3 audit, P1-1).
# The benign pair shares a helper; hazard's keeps its code.
def benign(cb):
    x = 0

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


if __name__ == "__main__":
    benign(lambda: None)
    hazard()
