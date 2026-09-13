import warnings
def w1(value):
    if value < 0:
        warnings.warn("negative", stacklevel=2)
    result = abs(value)
    print("w1", result)
    return result
def w2(value):
    if value < 0:
        warnings.warn("negative", stacklevel=2)
    result = abs(value)
    print("w2", result)
    return result
if __name__ == "__main__":
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        w1(-1); w2(-2)
    print([(str(c.message), c.filename.rsplit("/", 1)[-1]) for c in caught])
