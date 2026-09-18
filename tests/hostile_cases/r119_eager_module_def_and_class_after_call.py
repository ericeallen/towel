def f(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(later_fn_a)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(later_fn_b)
    total = sum(acc) + len(acc)
    return total * 2
print(f(False, [1, 2]), g(False, [3]))
def later_fn_a():
    pass
class later_fn_b:
    pass
if __name__ == "__main__":
    print("done")
