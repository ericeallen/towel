class Rec:
    def __init__(self, a, b, c): self.a, self.b, self.c = a, b, c
def c1(r):
    if not r.a:
        raise ValueError("missing")
    if len(r.a) < 2:
        raise ValueError("short")
    return r.a.upper()
def c2(r):
    if not r.b:
        raise ValueError("missing")
    if len(r.b) < 2:
        raise ValueError("short")
    return r.b.upper()
def c3(r):
    if not r.c:
        raise ValueError("missing")
    if len(r.c) < 2:
        raise ValueError("short")
    return r.c.upper()
if __name__ == "__main__":
    r = Rec("xx", "", "y")
    for f in (c1, c2, c3):
        try: print(f(r))
        except ValueError as e: print("err", e)
