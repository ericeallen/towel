def fa(xs):
    pass
    n = len(xs)
    total = 0
    for x in xs:
        total += x * n
    print("fa", n, total)
    return total
