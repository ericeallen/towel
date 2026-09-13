def local_helper(v):
    return v * 100
def fb(items):
    out = []
    for i in items:
        out.append(local_helper(i))
    out.append("b")
    return out
