def c1(base, items):
    scale = 3
    def inner(v):
        return v * scale + base
    result = [inner(i) for i in items]
    result.append(len(items))
    return result
def c2(base, items):
    scale = 5
    def inner(v):
        return v * scale + base
    result = [inner(i) for i in items]
    result.append(len(items))
    return result
if __name__ == "__main__":
    print(c1(1, [1, 2]), c2(2, [3]))
