import os
def n1(items):
    base = os.path.basename(__file__)
    total = sum(items)
    label = f"{base}:{total}"
    return label
def n2(items):
    base = os.path.basename(__file__)
    total = sum(items) * 2
    label = f"{base}:{total}"
    return label
if __name__ == "__main__":
    print(n1([1]), n2([1]))
