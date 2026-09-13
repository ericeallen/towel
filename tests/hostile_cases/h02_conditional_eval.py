def g1(d, k):
    out = []
    out.append("a")
    if k in d:
        out.append(d[k])
    out.append("z")
    return out
def g2(d, k):
    out = []
    out.append("a")
    if k in d:
        out.append(d[k + 1])
    out.append("z")
    return out
if __name__ == "__main__":
    print(g1({1: "x"}, 1), g1({}, 5), g2({2: "y"}, 1), g2({}, 5))
