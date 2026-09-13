def p1(items):
    acc = []
    for it in items:
        acc.append(it + 1)
    print("p1", acc)
def p2(items):
    res = []
    for x in items:
        res.append(x + 1)
    print("p2", res)
def p3(items):
    vals = []
    for y in items:
        vals.append(y + 1)
    print("p3", vals)
if __name__ == "__main__":
    p1([1]); p2([2]); p3([3])
