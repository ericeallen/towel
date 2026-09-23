# The positive control for r156: objects the block makes that nothing can
# look at, because the block only calls them, hands them to a builtin that
# calls them and keeps nothing, or consumes them where they stand.
def rank_first(rows, k):
    print("rank", 1)
    ordered = sorted(rows, key=lambda row: row[1] * k)
    best = max(rows, key=lambda row: row[0])
    total = sum(row[0] for row in rows)
    return ordered, best, total
def rank_second(rows, k):
    print("rank", 2)
    ordered = sorted(rows, key=lambda row: row[1] * k)
    best = max(rows, key=lambda row: row[0])
    total = sum(row[0] for row in rows)
    return ordered, best, total
def mapped_first(values, k):
    print("mapped", 1)
    scaled = list(map(lambda v: v * k, values))
    for kept in filter(lambda v: v > k, values):
        print("kept", kept)
    label = ", ".join(str(v) for v in scaled)
    return scaled, label, any(v > 2 for v in values)
def mapped_second(values, k):
    print("mapped", 2)
    scaled = list(map(lambda v: v * k, values))
    for kept in filter(lambda v: v > k, values):
        print("kept", kept)
    label = ", ".join(str(v) for v in scaled)
    return scaled, label, any(v > 2 for v in values)
def called_first(k, flag):
    print("called", 1)
    if flag:
        def step(v, by=k):
            return v + by
        twice = lambda v: step(step(v))
        first = next((v for v in range(10) if v > k), None)
        result = (twice(1), (lambda: k * 2)(), step(v=3), first)
    else:
        result = None
    return result
def called_second(k, flag):
    print("called", 2)
    if flag:
        def step(v, by=k):
            return v + by
        twice = lambda v: step(step(v))
        first = next((v for v in range(10) if v > k), None)
        result = (twice(1), (lambda: k * 2)(), step(v=3), first)
    else:
        result = None
    return result
if __name__ == "__main__":
    print(rank_first([(1, 3), (2, 1)], 1), rank_second([(3, 2), (1, 5)], -1))
    print(mapped_first([1, 2, 3], 2), mapped_second([4, 1], 1))
    print(called_first(2, True), called_second(5, True), called_first(1, False))
