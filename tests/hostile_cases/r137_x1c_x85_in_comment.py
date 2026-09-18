# sep  and nel  here
def f1(v):
    print("start f1")
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    acc.append(v * 4)
    return acc
def f2(v):
    print("start f2")
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    acc.append(v * 4)
    return acc + [2]
if __name__ == "__main__":
    print(f1(1), f2(2))
