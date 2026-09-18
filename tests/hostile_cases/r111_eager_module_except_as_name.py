try:
    pass
except ValueError as err_a:
    pass
try:
    1 / 0
except ZeroDivisionError as err_b:
    pass
def f(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(err_a)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(err_b)
    total = sum(acc) + len(acc)
    return total * 2
if __name__ == "__main__":
    print(f(False, [1, 2]), g(False, [3]))
