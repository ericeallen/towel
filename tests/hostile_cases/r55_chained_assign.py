def c1(items):
    lo = hi = items[0]
    for i in items:
        lo = min(lo, i)
        hi = max(hi, i)
    return lo, hi, "c1"
def c2(items):
    lo = hi = items[0]
    for i in items:
        lo = min(lo, i)
        hi = max(hi, i)
    return lo, hi, "c2"
if __name__ == "__main__":
    print(c1([3, 1, 2]), c2([5, 9]))
