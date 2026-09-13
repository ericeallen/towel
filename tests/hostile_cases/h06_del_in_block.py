def d1(v):
    x = v + 1
    print("have", x)
    del x
    print("deleted")
    try:
        return x
    except UnboundLocalError:
        return "unbound1"
def d2(v):
    x = v + 2
    print("have", x)
    del x
    print("deleted")
    try:
        return x
    except UnboundLocalError:
        return "unbound2"
if __name__ == "__main__":
    print(d1(1), d2(2))
