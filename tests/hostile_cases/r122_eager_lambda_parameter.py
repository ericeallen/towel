def f(flag, xs):
    key = lambda p: p
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(p)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    key = lambda q: q
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(q)
    total = sum(acc) + len(acc)
    return total * 2
if __name__ == "__main__":
    print(f(False, [1, 2]), g(False, [3]))
