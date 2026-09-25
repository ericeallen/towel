from . import a


def fb(xs):
    from .conf import SCALE
    total = 0
    for x in xs:
        total += x * SCALE
    print('fb', total)
    return total
