def f(flag, xs):
    ys = [i for i in xs]
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(i)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    ys = [j for j in xs]
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(j)
    total = sum(acc) + len(acc)
    return total * 2
if __name__ == "__main__":
    print(f(False, [1, 2]), g(False, [3]))
