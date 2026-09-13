def g(*xs): return xs
def t1(a, b):
    out = []
    out.append(g(*a, 1))
    out.append(len(out))
    out.append("t")
    return out
def t2(a, b):
    out = []
    out.append(g(b, 1))
    out.append(len(out))
    out.append("t")
    return out
if __name__ == "__main__":
    print(t1([7, 8], 9), t2([7, 8], 9))
