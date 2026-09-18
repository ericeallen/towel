def outer():
    gone_a = 1
    gone_b = 2
    def f(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(gone_a)
        total = sum(acc) + len(acc)
        return total
    def g(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(gone_b)
        total = sum(acc) + len(acc)
        return total * 2
    del gone_a, gone_b
    return f(False, [1, 2]), g(False, [3])
if __name__ == "__main__":
    print(outer())
