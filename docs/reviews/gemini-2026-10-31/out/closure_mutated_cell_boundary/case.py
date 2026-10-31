def f1(x):
    acc = x + 1
    def read_acc():
        return acc
    step = x * 2
    acc = step + 10
    res = acc + 1
    return res, read_acc()


def f2(x):
    acc = x + 1
    def read_acc():
        return acc
    step = x * 2
    acc = step + 20
    res = acc + 2
    return res, read_acc()
