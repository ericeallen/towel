def f1(n):
    buckets = []
    for i in range(n):
        row = [0]
        row.append(i)
        buckets.append(row)
    return buckets


def f2(n):
    buckets = []
    for i in range(n):
        row = [1, 2]
        row.append(i)
        buckets.append(row)
    return buckets
