counter = 0


def f1(x):
    global counter
    a = x + 1
    b = a * 2
    counter = counter + b
    return counter + 1


def f2(x):
    global counter
    a = x + 1
    b = a * 2
    counter = counter + b
    return counter + 2
