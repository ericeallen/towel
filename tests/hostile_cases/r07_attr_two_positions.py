class O:
    def __init__(self): self.x = 3; self.z = 5
def a1(o):
    out = []
    if o.x:
        out.append(o.x)
    out.append("end")
    return out
def a2(o):
    out = []
    if o.x:
        out.append(o.z)
    out.append("end")
    return out
if __name__ == "__main__":
    print(a1(O()), a2(O()))
