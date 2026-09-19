# ``helper`` is bound after ``main`` has already run once at import time, on
# a path that never read it. Passing ``helper`` eagerly at that call would
# raise NameError before the block could decide not to read it.
def main(items, flag):
    total = 0
    for item in items:
        total += item
    if flag:
        total = helper(total)
    print('main', total)
    return total
def other(items, flag):
    total = 0
    for item in items:
        total += item
    if flag:
        total = helper(total)
    print('other', total)
    return total * 2
EARLY = main([1, 2], False)
def helper(value):
    return value + 100
if __name__ == "__main__":
    print(EARLY, main([3], True), other([4], True), other([5], False))
