def g1(fn):
    err = None
    try:
        ret = fn()
    except ValueError as e:
        err = e
    if err is not None:
        return "err:" + str(err)
    return ret + 1
def g2(fn):
    err = None
    try:
        ret = fn()
    except ValueError as e:
        err = e
    if err is not None:
        return "err:" + str(err)
    return ret + 2
if __name__ == "__main__":
    def bad(): raise ValueError("boom")
    print(g1(lambda: 1), g1(bad), g2(lambda: 2), g2(bad))
