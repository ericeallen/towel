try:
    import no_such_mod_a as fast_a
    import no_such_mod_b as fast_b
except ImportError:
    pass
def f(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(fast_a)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(fast_b)
    total = sum(acc) + len(acc)
    return total * 2
if __name__ == "__main__":
    print(f(False, [1, 2]), g(False, [3]))
