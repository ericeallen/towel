def outer(x):

    def __extracted_func_0(c, state, y):
        a = y + 1
        c.bump()
        b = a + state
        return b

    state = 10

    class Counter:
        state = 0

        def bump(self):
            nonlocal state
            state += 1

    c = Counter()

    def f1(y):
        b = __extracted_func_0(c, state, y)
        return b + 1

    def f2(y):
        b = __extracted_func_0(c, state, y)
        return b + 2

    return f1(x), f2(x)
