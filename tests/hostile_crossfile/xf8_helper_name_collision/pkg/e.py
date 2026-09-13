def fe(items):
    out = []
    for i in items:
        out.append(i + 1)
    out.append("e")
    return out
def ge(items):
    out = {}
    for i in items:
        out[i] = i * 2
    out["e"] = len(items)
    return out
