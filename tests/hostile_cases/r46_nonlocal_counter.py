def o1(items):
    count = 0
    def inc():
        nonlocal count
        count += 1
    for i in items:
        inc()
        inc()
    print("o1", count)
    return count
def o2(items):
    count = 0
    def inc():
        nonlocal count
        count += 1
    for i in items:
        inc()
        inc()
    print("o2", count)
    return count
if __name__ == "__main__":
    print(o1([1, 2]), o2([3]))
