def p1(x):
    if x < 0:
        return "neg"
    y = x * 2
    z = y + 1
    if z > 100:
        return "big"
    print("after1", y, z)
    return y - z
def p2(x):
    if x < 0:
        return "neg"
    y = x * 3
    z = y + 1
    if z > 100:
        return "big"
    print("after2", y, z)
    return y - z
if __name__ == "__main__":
    print(p1(-1), p1(5), p1(60), p2(-2), p2(4), p2(50))
