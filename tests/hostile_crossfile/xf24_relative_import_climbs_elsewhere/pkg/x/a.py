def fa(v):
    print("pre", "a")
    try:
        from ..sub import VAL
    except ImportError as error:
        VAL = str(error)
    out = [VAL, v]
    out = out * 2
    return out
