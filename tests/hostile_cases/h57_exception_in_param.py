def x1(d):
    out = ["s"]
    try:
        out.append(d["a"])
    except KeyError:
        out.append("missing")
    out.append("e")
    return out
def x2(d):
    out = ["s"]
    try:
        out.append(d["b"])
    except KeyError:
        out.append("missing")
    out.append("e")
    return out
if __name__ == "__main__":
    print(x1({"a": 1}), x1({}), x2({"b": 2}), x2({}))
