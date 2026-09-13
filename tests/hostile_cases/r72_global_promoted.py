counter = 0
def g1(items):
    global counter
    for i in items:
        counter += i
    counter += 1
    print("g1", counter)
    return counter
def g2(items):
    global counter
    for i in items:
        counter += i
    counter += 1
    print("g2", counter)
    return counter
if __name__ == "__main__":
    print(g1([1, 2]), g2([3]), counter)
