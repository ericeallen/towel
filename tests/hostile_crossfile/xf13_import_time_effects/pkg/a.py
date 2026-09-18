print("importing a")
def build(xs):
    out = []
    for x in xs:
        out.append(x * 2)
        out.append(x * 3)
    out.append(len(out))
    return out
