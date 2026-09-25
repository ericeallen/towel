# A del later in the loop's body unbinds x for the next iteration. Passed
# eagerly, the read would raise at the call, before the iteration's first
# print; the block raised only when it reached the read.
def f1(xs):
    x = 1
    for i in xs:
        print("start", i)
        print("value", x + 1)
        print("done", i)
        del x
    return 0
def f2(ys):
    x = 2
    for i in ys:
        print("start", i)
        print("value", x + 1)
        print("done", i)
        del x
    return 1
if __name__ == "__main__":
    for f in (f1, f2):
        for a in ([1], [1, 2]):
            try: print(f(a))
            except Exception as e: print(type(e).__name__, e)
