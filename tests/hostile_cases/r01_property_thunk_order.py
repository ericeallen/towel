class P:
    def __init__(self): self.n = 0
    @property
    def val(self):
        self.n += 1; return self.n
def v1(p):
    print("n-before", p.n)
    acc = []
    acc.append(p.val)
    acc.append(p.val)
    return acc
def v2(p):
    print("n-before", p.n)
    acc = []
    acc.append(p.n)
    acc.append(p.n)
    return acc
if __name__ == "__main__":
    print(v1(P()), v2(P()))
