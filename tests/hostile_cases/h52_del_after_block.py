def k1(items):
    total = 0
    for i in items:
        total += i
    label = str(total)
    del label
    return total
def k2(items):
    total = 0
    for i in items:
        total += i * 2
    label = str(total)
    del label
    return total
if __name__ == "__main__":
    print(k1([1, 2]), k2([3]))
