def t1(items):
    total = 0
    for i in items:
        total += i
    cap = total + 1
    return list(map(lambda v: v + cap, items))
def t2(items):
    total = 0
    for i in items:
        total += i * 2
    cap = total + 1
    return list(map(lambda v: v + cap, items))
if __name__ == "__main__":
    print(t1([1, 2]), t2([3]))
