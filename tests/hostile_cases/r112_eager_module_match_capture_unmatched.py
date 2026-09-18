match 5:
    case [cap_a, cap_b]:
        pass
    case _:
        pass
def f(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(cap_a)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(cap_b)
    total = sum(acc) + len(acc)
    return total * 2
if __name__ == "__main__":
    print(f(False, [1, 2]), g(False, [3]))
