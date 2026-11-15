def gen1(items):
    for item in items:
        a = item + 1
        b = a * 2
        yield b + 10


def gen2(items):
    for item in items:
        a = item + 1
        b = a * 2
        yield b + 20
