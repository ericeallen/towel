class lazyclassproperty:
    def __init__(self, fn): self.fn = fn
    def __get__(self, obj, cls): return self.fn(cls)
class Chars:
    letters = "ba"
    digits = "21"
    @lazyclassproperty
    def ident(cls):
        return "".join(sorted(set(cls.letters) | set("xy_")))
    @lazyclassproperty
    def body(cls):
        return "".join(sorted(set(cls.digits) | set("0·")))
    @lazyclassproperty
    def both(cls):
        return "".join(sorted(set(cls.letters) | set("q")))
if __name__ == "__main__":
    print(Chars.ident, Chars.body, Chars.both, Chars().ident)
