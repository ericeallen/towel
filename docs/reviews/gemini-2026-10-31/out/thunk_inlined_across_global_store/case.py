state = 10


def __extracted_func_0(__param_0, x):
    global state
    state = 99
    items = __param_0 + x
    b = items * 2
    return b


def get_state():
    return state


def f1(x):
    return __extracted_func_0(get_state(), x)


def f2(x):
    return __extracted_func_0(x * 3, x)
