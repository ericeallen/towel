def x1(v):
    try:
        r = 1 / v
    except ZeroDivisionError as e:
        msg = str(e)
        r = None
    else:
        msg = "fine"
    print("x1", r)
    return msg
def x2(v):
    try:
        r = 2 / v
    except ZeroDivisionError as e:
        msg = str(e)
        r = None
    else:
        msg = "fine"
    print("x2", r)
    return msg
if __name__ == "__main__":
    print(x1(1), x1(0), x2(1), x2(0))
