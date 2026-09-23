# The decorator wraps every function of the class, a helper included.
import functools

def logged(cls):
    for name, fn in list(vars(cls).items()):
        if callable(fn) and not name.startswith("__"):
            def wrap(fn, name):
                @functools.wraps(fn)
                def wrapper(*args, **kwargs):
                    print("call", name)
                    return fn(*args, **kwargs)
                return wrapper
            setattr(cls, name, wrap(fn, name))
    return cls

@logged
class A:
    k = 0
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
    def b(self, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
if __name__ == "__main__":
    print(A().a([1, 5, 9]), A().b([1, 5, 9]))
