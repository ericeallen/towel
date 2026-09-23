# `x: int` without a value declares x but assigns nothing: the x read after
# it is still the one the caller bound. Counted as a binding, it was never
# passed, and the helper raised UnboundLocalError.
def annotated_first(v):
    x = v + 1
    x: int
    y = x * 2
    print(y, "one")
    return y
def annotated_second(v):
    w = [v]
    x = w[0] * 3
    x: int
    y = x * 2
    print(y, "two")
    return y
if __name__ == "__main__":
    print(annotated_first(1), annotated_second(2))
