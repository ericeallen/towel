# A local bound on one path only, read by the shared block. Passed as a thunk,
# the unfilled closure cell raises NameError where the original raised
# UnboundLocalError, so the handler below stops matching.
def f(v, flag):
    if flag:
        x = v
    a = v + 1
    b = a * 2
    c = b + x
    return c


def g(v, flag):
    if flag:
        x = v
    a = v + 2
    b = a * 2
    c = b + x
    return c


if __name__ == "__main__":
    for fn in (f, g):
        for fl in (True, False):
            try:
                print(fn(1, fl))
            except UnboundLocalError as error:
                print("UnboundLocalError", error)
            except NameError as error:
                print("NameError", error)
