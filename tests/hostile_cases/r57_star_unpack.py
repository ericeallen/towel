def s1(items):
    first, *rest = items
    rest = [r * 2 for r in rest]
    print("s1", first)
    return first, rest
def s2(items):
    first, *rest = items
    rest = [r * 2 for r in rest]
    print("s2", first)
    return first, rest
if __name__ == "__main__":
    print(s1([1, 2, 3]), s2([4]))
