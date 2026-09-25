# Decorators the project defines that are plain wrappers leave the body
# alone: one calls the function with its own arguments after printing, one
# keeps it in a registry and returns it. Both functions still refactor.
import functools

REGISTRY = {}


def logged(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print("call", fn.__name__)
        return fn(*args, **kwargs)

    return wrapper


def register(fn):
    REGISTRY[fn.__name__] = fn
    return fn


@logged
def a(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


@register
@logged
def b(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5


if __name__ == "__main__":
    print(a([1, 5, 9]), b([2, 4]), sorted(REGISTRY), REGISTRY["b"]([1]))
