import math
def fa(items, factor):
    total = sum(items)
    out = [math.floor(i * factor / total) for i in items]
    out.append(len(items))
    return out
