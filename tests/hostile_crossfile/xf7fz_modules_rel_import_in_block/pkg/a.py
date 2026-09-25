def fa(xs):
    from .conf import SCALE
    total = 0
    for x in xs:
        total += x * SCALE
    print('fa', total)
    return total
