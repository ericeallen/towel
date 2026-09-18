def scale_all(items, factor):
    scale = lambda value: value * factor
    out = []
    for item in items:
        out.append(scale(item))
    print("scaled", out)
    return out
def weigh_all(items, factor):
    scale = lambda other: other * factor
    out = []
    for item in items:
        out.append(scale(item))
    print("scaled", out)
    return out
def shadowed(items, factor):
    # The free name ``value`` outside the lambda is not the parameter inside it.
    value = 100
    scale = lambda value: value * factor
    out = []
    for item in items:
        out.append(scale(item) + value)
    print("scaled", out)
    return out
if __name__ == "__main__":
    print(scale_all([1, 2, 3], 2), weigh_all([4, 5], 3), shadowed([6], 1))
