def outer(ready):
    if ready:
        opt_a = 1
        opt_b = 2
    def f(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(opt_a)
        total = sum(acc) + len(acc)
        return total
    def g(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(opt_b)
        total = sum(acc) + len(acc)
        return total * 2
    return f(False, [1, 2]), g(False, [3])
if __name__ == "__main__":
    print(outer(False))
