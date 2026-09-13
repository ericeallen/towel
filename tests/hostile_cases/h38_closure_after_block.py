def a1(items):
    total = 0
    for i in items:
        total += i
    limit = total * 2
    def check(v):
        return v < limit
    return [check(i) for i in items]
def a2(items):
    total = 0
    for i in items:
        total += i
    limit = total * 3
    def check(v):
        return v < limit
    return [check(i) for i in items]
if __name__ == "__main__":
    print(a1([1, 2, 3]), a2([1, 5]))
