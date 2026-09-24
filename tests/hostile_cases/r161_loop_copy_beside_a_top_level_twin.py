# The loop's copy binds w, which the next iteration reads before the copy runs
# again, so it cannot move without returning w. The top-level copy, the same
# code, is judged first; its bindings and verdicts once stood for the loop's.
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


if __name__ == "__main__":
    f([1, 2])
    g([1])
