class P:
    def __init__(self): self.n = 0
    @property
    def val(self):
        self.n += 1; return self.n
def v1(p):
    print("start")
    acc = []
    acc.append(1)
    acc.append(p.val)
    acc.append(p.n)
    return acc
def v2(p):
    print("start")
    acc = []
    acc.append(1)
    acc.append(p.n)
    acc.append(p.n)
    return acc
if __name__ == "__main__":
    print(v1(P()), v2(P()))
