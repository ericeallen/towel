def make_a():
    class Wrapper:
        def __init__(self): self.hits = 0
        def get(self, key):
            self.hits += 1
            if key is None:
                raise KeyError("none")
            return ("a", key, self.hits)
    return Wrapper()
def make_b():
    class Wrapper:
        def __init__(self): self.hits = 10
        def get(self, key):
            self.hits += 1
            if key is None:
                raise KeyError("none")
            return ("b", key, self.hits)
    return Wrapper()
if __name__ == "__main__":
    a, b = make_a(), make_b(); print(a.get(1), b.get(2), a.get(3))
