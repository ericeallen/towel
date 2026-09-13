def n1(k, j):
    out = []
    out.append(k)
    out.append(k)
    out.append("end")
    return out
def n2(k, j):
    out = []
    out.append(k)
    out.append(j)
    out.append("end")
    return out
if __name__ == "__main__":
    print(n1(1, 2), n2(1, 2))
