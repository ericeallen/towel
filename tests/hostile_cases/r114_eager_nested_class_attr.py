class Outer:
    OUT_A = 1
    OUT_B = 2
    class Inner:
        IN_A = 3
        def f(self, flag, xs):
            acc = [x + 1 for x in xs]
            if flag:
                acc.append(OUT_A)
            total = sum(acc) + len(acc)
            return total
        def g(self, flag, xs):
            acc = [x + 1 for x in xs]
            if flag:
                acc.append(OUT_B)
            total = sum(acc) + len(acc)
            return total * 2
if __name__ == "__main__":
    print(Outer.Inner().f(False, [1, 2]), Outer.Inner().g(False, [3]))
