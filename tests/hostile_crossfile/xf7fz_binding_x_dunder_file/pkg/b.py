import os

from . import a


def fb(ys):
    here = os.path.basename(__file__)
    t = 0
    for x in ys:
        t += x
    print("fb", here, t)
    return here


def use():
    return a.fa([1])
