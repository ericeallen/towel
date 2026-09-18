def make():
    class K:
        ATTR_A = 1
        ATTR_B = 2
        def f(self, flag, xs):
            acc = [x + 1 for x in xs]
            if flag:
                acc.append(ATTR_A)
            total = sum(acc) + len(acc)
            return total
        def g(self, flag, xs):
            acc = [x + 1 for x in xs]
            if flag:
                acc.append(ATTR_B)
            total = sum(acc) + len(acc)
            return total * 2
    return K
if __name__ == "__main__":
    K = make()
    print(K().f(False, [1, 2]), K().g(False, [3]))
