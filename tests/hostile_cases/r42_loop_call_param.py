import itertools
c = itertools.count()
def w1(n):
    out = []
    for _ in range(n):
        out.append(next(c))
    out.append("w")
    return out
def w2(n):
    out = []
    for _ in range(n):
        out.append(next(c) * 100)
    out.append("w")
    return out
if __name__ == "__main__":
    print(w1(2), w2(2))
