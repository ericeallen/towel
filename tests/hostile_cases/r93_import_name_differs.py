import sys
import types
sys.modules["r93mod"] = types.SimpleNamespace(alpha="A", beta="B", gamma="C")
import r93mod
class Recorder:
    def __init__(self, message):
        self.message = message
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
class Tests:
    def record(self, message):
        return Recorder(message)
    def check(self, left, right):
        print("check", left, right, left == right)
    def test_alpha(self):
        message = "loading alpha"
        with self.record(message) as w:
            from r93mod import alpha
        self.check(alpha, r93mod.alpha)
        self.check(w.message, message)
    def test_beta(self):
        message = "loading beta"
        with self.record(message) as w:
            from r93mod import beta
        self.check(beta, r93mod.beta)
        self.check(w.message, message)
    def test_gamma(self):
        message = "loading gamma"
        with self.record(message) as w:
            from r93mod import gamma
        self.check(gamma, r93mod.gamma)
        self.check(w.message, message)
if __name__ == "__main__":
    t = Tests()
    t.test_alpha()
    t.test_beta()
    t.test_gamma()
