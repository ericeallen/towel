state = 10


def get_state():
    return state


def f1(x):
    global state
    state = 99
    items = get_state() + x
    b = items * 2
    return b


def f2(x):
    global state
    state = 99
    items = (x * 3) + x
    b = items * 2
    return b
