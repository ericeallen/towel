import os
def fb(items):
    out = []
    for i in items:
        out.append(i + 1)
    out.append(os.path.basename(__file__))
    return out
