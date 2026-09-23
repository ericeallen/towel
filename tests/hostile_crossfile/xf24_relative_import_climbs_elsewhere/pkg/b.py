# ``..sub`` climbs from pkg.x to pkg in a.py, but from pkg past the top
# of the package here, which raises ImportError instead.
def fb(v):
    print("pre", "b")
    try:
        from ..sub import VAL
    except ImportError as error:
        VAL = str(error)
    out = [VAL, v]
    out = out * 2
    return out
