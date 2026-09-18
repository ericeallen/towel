def f(xs):
    total = sum(xs)
    count = len(xs)
    scaled = [x / total for x in xs]
    return f"f {scaled} {count}"
def g(xs):
    total = sum(xs)
    count = len(xs)
    scaled = [x / total for x in xs]
    return f"g {scaled} {count}"
def h(ys):
    amount = sum(ys)
    size = len(ys)
    parts = [y / amount for y in ys]
    return f"h {parts} {size} {amount}"
def k(xs):
    total = sum(xs)
    count = len(xs)
    scaled = [x / total for x in xs]
    print(total)
    return f"k {scaled} {count}"
if __name__ == "__main__":
    print(f([1, 2]), g([3, 4]), h([5, 5]), k([2, 8]))
