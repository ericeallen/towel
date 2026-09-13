def f1(items):
    zeta = items[0]
    alpha = items[-1]
    total = zeta + alpha
    print("f1", total)
    label = f"{zeta}:{alpha}"
    return label, total
def f2(items):
    lo = items[0]
    hi = items[-1]
    total = lo + hi
    print("f2", total)
    label = str(lo) + "," + str(hi)
    return label, total
if __name__ == "__main__":
    print(f1([1, 2, 3]), f2([10, 5]))
