class Chars:
    def __init__(self, letters, digits): self.letters = letters; self.digits = digits
    @property
    def noisy(self):
        print("noisy"); return self.letters
def ident(c):
    chars = set(c.noisy) | set("xy_")
    ordered = sorted(chars)
    return "".join(ordered)
def body(c):
    chars = set(c.digits) | set("0")
    ordered = sorted(chars)
    return "".join(ordered)
def both(c):
    chars = set(c.noisy) | set("q")
    ordered = sorted(chars)
    return "".join(ordered)
if __name__ == "__main__":
    c = Chars("ba", "21"); print(ident(c), body(c), both(c))
