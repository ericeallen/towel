calls = []
def side(tag): calls.append(tag); return True
def s1(flag):
    calls.append("s1")
    ok = flag and side("a")
    calls.append("after")
    return ok
def s2(flag):
    calls.append("s2")
    ok = flag and side("b")
    calls.append("after")
    return ok
if __name__ == "__main__":
    print(s1(False), s1(True), s2(False), s2(True), calls)
