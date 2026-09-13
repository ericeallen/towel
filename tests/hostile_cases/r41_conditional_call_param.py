log = []
def fa(): log.append("fa"); return 1
def fb(): log.append("fb"); return 2
def q1(flag):
    out = []
    if flag:
        out.append(fa())
    out.append("done")
    return out
def q2(flag):
    out = []
    if flag:
        out.append(fb())
    out.append("done")
    return out
if __name__ == "__main__":
    print(q1(False), q2(True), log)
