def f(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(json)
    total = sum(acc) + len(acc)
    return total
def g(flag, xs):
    acc = [x + 1 for x in xs]
    if flag:
        acc.append(pickle)
    total = sum(acc) + len(acc)
    return total * 2
print(f(False, [1, 2]), g(False, [3]))
import json
import pickle
if __name__ == "__main__":
    print("done")
