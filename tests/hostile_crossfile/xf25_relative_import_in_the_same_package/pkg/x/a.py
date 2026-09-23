def fa(v):
    print("pre", "a")
    from .sub import VAL
    total = VAL + v
    total = total * 2
    return total
