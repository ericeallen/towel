state = 10


def _extracted_func_0(__param_0, x):
    a = x + 1
    bump()
    b = a * 2
    return b + __param_0


def bump():
    global state
    state += 1


class Worker:
    state = 0

    def f1(self, x):
        return _extracted_func_0(state, x)

    def f2(self, x):
        return _extracted_func_0(100, x)
