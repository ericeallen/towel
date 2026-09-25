# Each block ends at the first statement of a line: the call must keep the
# statement after it, which differs between the sites.
log = []
def g1(items):
    total = sum(items)
    print("g1 total", total)
    count = len(items); log.append(("g1", count))
    return total * 10 + count
def g2(items):
    total = sum(items)
    print("g2 total", total)
    count = len(items); scaled = count * 3
    return total + scaled
if __name__ == "__main__":
    print(g1([1, 2]), g2([3, 4, 5]), log)
