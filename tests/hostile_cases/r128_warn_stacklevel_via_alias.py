import warnings
from warnings import warn as w
def wn1(v):
    log = [v]
    log.append(v * 2)
    w("careful", stacklevel=2)
    log.append(v * 3)
    return log
def wn2(v):
    log = [v]
    log.append(v * 2)
    w("careful", stacklevel=2)
    log.append(v * 3)
    return log + [2]
def wn3(v):
    log = [v]
    log.append(v * 2)
    warnings.warn("plain")
    log.append(v * 3)
    return log
def wn4(v):
    log = [v]
    log.append(v * 2)
    warnings.warn("plain")
    log.append(v * 3)
    return log + [4]
if __name__ == "__main__":
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        print(wn1(1), wn2(2), wn3(3), wn4(4))
    print([(str(c.message), c.lineno) for c in caught])
