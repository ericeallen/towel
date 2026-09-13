import itertools
c = itertools.count()
def nxt(): return next(c)
def r1(n):
    acc = []
    for i in range(n):
        acc.append(i)
        acc.append(nxt())
    acc.append("end")
    return acc
def r2(n):
    acc = []
    for i in range(n):
        acc.append(i)
        acc.append(nxt() * 10)
    acc.append("end")
    return acc
if __name__ == "__main__":
    print(r1(3), r2(2))
