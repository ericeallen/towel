import os


def fa(xs):
    here = os.path.basename(__file__)
    t = 0
    for x in xs:
        t += x
    print("fa", here, t)
    return here
