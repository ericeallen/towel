def outer(x):
    state = 10

    class Counter:
        state = 0

        def bump(self):
            nonlocal state
            state += 1

    c = Counter()

    def f1(y):
        a = y + 1
        c.bump()
        b = a + state
        return b + 1

    def f2(y):
        a = y + 1
        c.bump()
        b = a + state
        return b + 2

    return f1(x), f2(x)
