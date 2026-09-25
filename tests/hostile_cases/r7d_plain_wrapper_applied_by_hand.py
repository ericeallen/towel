# Plain wrappers applied by hand leave the body alone: a decorator the
# project defines, a known factory, and a property built from its accessors.
# Each function still refactors.
import functools


def logged(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print("call", fn.__name__)
        return fn(*args, **kwargs)

    return wrapper


def a(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


def b(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5


class Box:
    def __init__(self):
        self.stored = 0

    def get_total(self):
        total = 0
        for item in (1, 5, 9):
            total = total + item * 2
        result = total + 7
        return result * 3

    def set_total(self, items):
        total = 0
        for item in items:
            total = total + item * 2
        result = total + 7
        self.stored = result * 5

    total = property(get_total, set_total)


logged_a = logged(a)
cached_b = functools.lru_cache(maxsize=None)(b)


if __name__ == "__main__":
    box = Box()
    box.total = [2, 4]
    print(logged_a([1, 5, 9]), cached_b((2, 4)), box.total, box.stored)
