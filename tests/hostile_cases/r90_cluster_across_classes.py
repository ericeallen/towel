class Test:
    def flakes(self, *sources):
        return [len(s) for s in sources]
    def test_a(self):
        r = self.flakes("import a")
        r += self.flakes("import a; a")
        r += self.flakes("import a as b")
        return r
    def test_b(self):
        r = self.flakes("from a import b")
        r += self.flakes("from a import b; b")
        r += self.flakes("from a import b as c")
        return r
class TestOther:
    def flakes(self, *sources):
        return [2 * len(s) for s in sources]
    def test_c(self):
        r = self.flakes("x = 1")
        r += self.flakes("x = 1; x")
        r += self.flakes("y = 2")
        return r
def free_function(flakes):
    r = flakes("z")
    r += flakes("zz")
    r += flakes("zzz")
    return r
if __name__ == "__main__":
    print(Test().test_a(), Test().test_b(), TestOther().test_c(), free_function(TestOther().flakes))
