def f(xs):
    fs = []
    for x in xs:
        fs.append(lambda: x)
    k = len(fs) + 1
    print("f")
    x = 99
    return [h() for h in fs], k
def g(xs):
    fs = []
    for x in xs:
        fs.append(lambda: x)
    k = len(fs) + 1
    x = 42
    print("g")
    return [h() for h in fs], k * 2
if __name__ == "__main__":
    print(f([1, 2, 3]), g([4, 5]))
