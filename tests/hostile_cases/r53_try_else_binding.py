def t1(v):
    try:
        r = 10 / v
    except ZeroDivisionError:
        r = None
    else:
        note = "ok"
        r = r + 1
    print("t1", r)
    try:
        return note
    except UnboundLocalError:
        return "no-note"
def t2(v):
    try:
        r = 20 / v
    except ZeroDivisionError:
        r = None
    else:
        note = "ok"
        r = r + 1
    print("t2", r)
    try:
        return note
    except UnboundLocalError:
        return "no-note"
if __name__ == "__main__":
    print(t1(1), t1(0), t2(2), t2(0))
