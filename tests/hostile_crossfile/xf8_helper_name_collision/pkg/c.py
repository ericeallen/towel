def fc(items):
    out = {}
    for i in items:
        out[i] = i * 2
    out["c"] = len(items)
    return out
