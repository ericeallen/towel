def j1(items):
    out = []
    for i in items:
        out.append(i)
    out.append(sorted(items))
    out.append(len(out))
    return out
def j2(items):
    out = []
    for i in items:
        out.append(i)
    out.append(list(reversed(items)))
    out.append(len(out))
    return out
if __name__ == "__main__":
    print(j1([2, 1]), j2([2, 1]))
