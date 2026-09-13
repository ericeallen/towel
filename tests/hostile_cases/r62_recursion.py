def r1(n, acc):
    if n <= 0:
        return acc
    acc.append(n)
    acc.append(n * 10)
    return r1(n - 1, acc)
def r2(n, acc):
    if n <= 0:
        return acc
    acc.append(n)
    acc.append(n * 10)
    return r2(n - 1, acc)
if __name__ == "__main__":
    print(r1(2, []), r2(3, ["x"]))
