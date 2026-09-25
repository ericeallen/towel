import pkg.a


def fb(xs):
    total = 0
    for x in xs:
        total += x * 3
    print("fb", total)
    return total + 1


if __name__ == '__main__':
    print(fb([1, 2]))
