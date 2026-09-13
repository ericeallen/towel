def fd(items):
    out = {}
    for i in items:
        out[i] = i * 2
    out["d"] = len(items)
    return out
