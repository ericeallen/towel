def n1(items):
    total: int = 0
    for i in items:
        total += i
    label: str = f"n1={total}"
    return label
def n2(items):
    total: int = 0
    for i in items:
        total += i
    label: str = f"n2={total}"
    return label
if __name__ == "__main__":
    print(n1([1, 2]), n2([3]))
