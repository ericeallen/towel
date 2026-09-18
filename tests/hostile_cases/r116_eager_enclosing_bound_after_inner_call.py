def outer():
    def f(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(late_a)
        total = sum(acc) + len(acc)
        return total
    def g(flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(late_b)
        total = sum(acc) + len(acc)
        return total * 2
    r = (f(False, [1, 2]), g(False, [3]))
    late_a = 1
    late_b = 2
    return r
if __name__ == "__main__":
    print(outer())
