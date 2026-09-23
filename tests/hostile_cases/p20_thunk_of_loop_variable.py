# A loop variable is unbound after a loop that never ran.
def f(items):
    for it in items:
        last = it
    a = len(items) + 1
    b = a * 2
    c = b + last
    return c


def g(items):
    for it in items:
        last = it
    a = len(items) + 2
    b = a * 2
    c = b + last
    return c


if __name__ == "__main__":
    for fn in (f, g):
        for s in ([1, 2], []):
            try:
                print(fn(s))
            except UnboundLocalError:
                print("UnboundLocalError")
            except NameError:
                print("NameError")
