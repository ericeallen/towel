class Box:
    def __init__(self): self.a = None; self.b = None
def i1(box, v):
    checked = v if v is not None else 0
    box.a = checked
    box.a = box.a + 1
    return box
def i2(box, v):
    checked = v if v is not None else 0
    box.b = checked
    box.b = box.b + 1
    return box
if __name__ == "__main__":
    print(vars(i1(Box(), 1)), vars(i2(Box(), None)))
