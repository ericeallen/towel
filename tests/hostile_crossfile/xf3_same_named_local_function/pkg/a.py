def local_helper(v):
    return v + 1
def fa(items):
    out = []
    for i in items:
        out.append(local_helper(i))
    out.append("a")
    return out
