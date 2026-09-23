def aggregate(items):
    print("aggregate")
    total = 0
    for value in items:
        total += value * 2
    return total + 1
