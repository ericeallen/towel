trace = []
def compute_a():
    trace.append("compute_a"); return 1
def compute_b():
    trace.append("compute_b"); return 2
def f1(items):
    trace.append("start1")
    total = 0
    for it in items:
        total += it
    x = compute_a()
    trace.append("mid1")
    return total + x
def f2(items):
    trace.append("start2")
    total = 0
    for it in items:
        total += it
    x = compute_b()
    trace.append("mid2")
    return total + x
if __name__ == "__main__":
    print(f1([1, 2]), f2([3])); print(trace)
