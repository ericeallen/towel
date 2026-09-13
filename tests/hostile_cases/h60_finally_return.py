def y1(v):
    try:
        r = 1 / v
        return r
    except ZeroDivisionError:
        return "zero"
    finally:
        print("fin1")
def y2(v):
    try:
        r = 2 / v
        return r
    except ZeroDivisionError:
        return "zero"
    finally:
        print("fin2")
if __name__ == "__main__":
    print(y1(1), y1(0), y2(1), y2(0))
