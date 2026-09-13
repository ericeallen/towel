def outer_a(items):
    out = []
    for i in items:
        if i > 0:
            out.append(i)
            out.append(i * 2)
    out.append("a")
    return out
def outer_b(items):
    out = []
    for i in items:
        if i > 0:
            out.append(i)
            out.append(i * 2)
    out.append("b")
    return out
def inner_c(i, out):
    if i > 0:
        out.append(i)
        out.append(i * 2)
    return out
if __name__ == "__main__":
    print(outer_a([1, -1, 2]), outer_b([3]), inner_c(4, []), inner_c(-4, ["z"]))
