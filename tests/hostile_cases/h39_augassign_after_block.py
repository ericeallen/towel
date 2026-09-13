def b1(items):
    total = 0
    for i in items:
        total += i
    count = len(items)
    count += 1
    return total, count
def b2(items):
    total = 0
    for i in items:
        total += i * 2
    count = len(items)
    count += 1
    return total, count
if __name__ == "__main__":
    print(b1([1, 2]), b2([3]))
