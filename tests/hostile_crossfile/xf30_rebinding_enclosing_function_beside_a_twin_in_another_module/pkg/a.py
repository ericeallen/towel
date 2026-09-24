# The benign twin of b.py's hazard: nothing rebinds x, so f and g share a helper.
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
