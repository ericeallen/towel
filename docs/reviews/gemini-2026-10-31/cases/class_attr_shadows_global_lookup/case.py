state = 10


def bump():
    global state
    state += 1


class Worker:
    state = 0

    def f1(self, x):
        a = x + 1
        bump()
        b = a * 2
        return b + state

    def f2(self, x):
        a = x + 1
        bump()
        b = a * 2
        return b + 100
