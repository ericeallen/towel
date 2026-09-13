def first(scope, key):
    err = None
    try:
        cur = scope[key]
    except KeyError as e:
        err = ("missing", key)
    if err:
        raise ValueError(err)
    return cur
def second(target, spec):
    err = None
    try:
        ret = target[spec]
    except KeyError as e:
        err = ("absent", spec)
    if err:
        raise ValueError(err)
    return ret
if __name__ == "__main__":
    for f, arg in ((first, {"a": 1}), (second, {"b": 2})):
        print(f(arg, list(arg)[0]))
        try:
            f(arg, "zz")
        except ValueError as exc:
            print("caught", exc)
