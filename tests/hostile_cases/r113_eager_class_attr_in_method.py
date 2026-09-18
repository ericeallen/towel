class C:
    LIMIT_A = 1
    LIMIT_B = 2
    def f(self, flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(LIMIT_A)
        total = sum(acc) + len(acc)
        return total
    def g(self, flag, xs):
        acc = [x + 1 for x in xs]
        if flag:
            acc.append(LIMIT_B)
        total = sum(acc) + len(acc)
        return total * 2
if __name__ == "__main__":
    print(C().f(False, [1, 2]), C().g(False, [3]))
