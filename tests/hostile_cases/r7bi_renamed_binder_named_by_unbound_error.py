# Blocks equal up to the names they bind share one helper spelled like the
# first. Where a binder can be read unbound (after a del, after the except
# clause that bound it) UnboundLocalError names it, so the second site's
# message would name the first site's variable.
def deleted_one(xs, flag):
    item = xs[0]
    if flag:
        del item
    r = item * 2
    print("deleted_one", r)
    return r
def deleted_two(ys, flag):
    elem = ys[0]
    if flag:
        del elem
    r = elem * 2
    print("deleted_two", r)
    return r
def handler_one(v, flag):
    print("start", v)
    try:
        r = 1 / v
    except ZeroDivisionError as err1:
        r = 0
    if flag:
        print(err1)
    return r
def handler_two(v, flag):
    print("start", v)
    try:
        r = 1 / v
    except ZeroDivisionError as err2:
        r = 0
    if flag:
        print(err2)
    return r
if __name__ == "__main__":
    for function, arguments in (
        (deleted_one, (([1], False), ([1], True))),
        (deleted_two, (([1], False), ([1], True))),
        (handler_one, ((0, True), (1, True), (0, False))),
        (handler_two, ((0, True), (1, True), (0, False))),
    ):
        for argument in arguments:
            try:
                print(function.__name__, argument, "->", function(*argument))
            except Exception as error:
                print(function.__name__, argument, "raised", type(error).__name__, error)
