def e1(v):
    e = "orig"
    try:
        r = 1 / v
    except ZeroDivisionError as e:
        r = None
    print("r", r)
    try:
        return e
    except UnboundLocalError:
        return "e-unbound1"
def e2(v):
    e = "orig"
    try:
        r = 2 / v
    except ZeroDivisionError as e:
        r = None
    print("r", r)
    try:
        return e
    except UnboundLocalError:
        return "e-unbound2"
if __name__ == "__main__":
    print(e1(1), e1(0), e2(1), e2(0))
