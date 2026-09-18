def ff1(v):
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    return acc

def ff2(v):
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    return acc + [2]
if __name__ == "__main__":
    print(ff1(1), ff2(2))
