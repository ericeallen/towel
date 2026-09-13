def u1(flag):
    if flag:
        val = 1
    other = 2
    print("u1")
    try:
        return val + other
    except UnboundLocalError:
        return "unbound"
def u2(flag):
    if flag:
        val = 10
    other = 2
    print("u2")
    try:
        return val + other
    except UnboundLocalError:
        return "unbound"
if __name__ == "__main__":
    print(u1(True), u1(False), u2(True), u2(False))
