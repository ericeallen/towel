def g(*xs): return sum(xs)
def s1(a, b):
    out = []
    out.append(g(*a))
    out.append(len(a))
    out.append("s")
    return out
def s2(a, b):
    out = []
    out.append(g(*(a + b)))
    out.append(len(a))
    out.append("s")
    return out
if __name__ == "__main__":
    print(s1([1, 2], [3]), s2([1, 2], [3]))
