import warnings
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
        warnings.simplefilter("default")
        print(wn3(3), wn4(4), wn3(5), wn4(6))
    print(len(caught), [str(c.message) for c in caught])
