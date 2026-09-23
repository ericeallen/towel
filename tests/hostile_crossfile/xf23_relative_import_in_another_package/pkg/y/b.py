# ``from .sub`` means pkg.y.sub here and pkg.x.sub in a.py: a helper
# holding the import would read a.py's package for both callers.
def fb(v):
    print("pre", "b")
    from .sub import VAL
    total = VAL + v
    total = total * 2
    return total
