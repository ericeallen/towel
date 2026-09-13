from .util2 import scale
def fb(items):
    out = []
    for i in items:
        out.append(scale(i))
    out.append("b")
    return out
