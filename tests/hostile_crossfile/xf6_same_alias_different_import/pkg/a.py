from .util1 import scale
def fa(items):
    out = []
    for i in items:
        out.append(scale(i))
    out.append("a")
    return out
