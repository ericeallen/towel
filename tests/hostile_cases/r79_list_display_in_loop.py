def l1(rows):
    acc = []
    for r in rows:
        acc.append([])
        acc[-1].append(r)
    acc.append("l1")
    return acc
def l2(rows):
    acc = []
    for r in rows:
        acc.append(())
        acc[-1:] = [(r,)]
    acc.append("l2")
    return acc
def l3(rows):
    acc = []
    for r in rows:
        acc.append([])
        acc[-1].append(r * 2)
    acc.append("l3")
    return acc
if __name__ == "__main__":
    print(l1([1, 2]), l2([3]), l3([4, 5]))
