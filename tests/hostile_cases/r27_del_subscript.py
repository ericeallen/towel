def d1(d):
    d["tmp"] = 1
    total = sum(d.values())
    del d["tmp"]
    return total, sorted(d)
def d2(d):
    d["tmp"] = 1
    total = sum(d.values())
    del d["x"]
    return total, sorted(d)
if __name__ == "__main__":
    print(d1({"x": 2}), d2({"x": 2}))
