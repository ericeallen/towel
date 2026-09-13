log = []
def ca(): log.append("ca"); return True
def cb(): log.append("cb"); return False
def q1(x):
    out = []
    out.append(x if ca() else -x)
    out.append(len(log))
    out.append("q1")
    return out
def q2(x):
    out = []
    out.append(x if cb() else -x)
    out.append(len(log))
    out.append("q2")
    return out
if __name__ == "__main__":
    print(q1(1), q2(2), log)
