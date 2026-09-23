# Both modules are in pkg.x, so ``.sub`` is the same module from either.
def fb(v):
    print("pre", "b")
    from .sub import VAL
    total = VAL + v
    total = total * 2
    return total
