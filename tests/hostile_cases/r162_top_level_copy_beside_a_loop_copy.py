# As r161, but g returns w as well, so the top-level copy and g's share a
# helper returning it. The loop's copy keeps its code.
def f(items):
    w = len(items)
    print("w", w)
    print("x")
    for _ in range(3):
        print("before", w)
        items.append(0)
        w = len(items)
        print("w", w)
        print("x")


def g(items):
    w = len(items)
    print("w", w)
    print("x")
    return w


if __name__ == "__main__":
    f([1, 2])
    print(g([1]))
