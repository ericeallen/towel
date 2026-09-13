def q1(items):
    total = 0
    for i in items:
        total += i
    n = len(items); avg = total / n if n else 0
    return avg
def q2(items):
    total = 0
    for i in items:
        total += i * 2
    n = len(items); avg = total / n if n else 0
    return avg
if __name__ == "__main__":
    print(q1([1, 2]), q2([3]))
