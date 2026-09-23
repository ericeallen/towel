# A function, lambda or generator made by moved code is made by the helper:
# its __qualname__ names the helper, and a lambda built from a unified
# template has the template's parameter names. Wherever such an object
# outlives the block, or is looked at other than by calling it, the block
# must stay.
import collections
import inspect
def make_first(k):
    print("make", 1)
    g = lambda: k + 1
    print(g())
    return g
def make_second(k):
    print("make", 2)
    g = lambda: k + 2
    print(g())
    return g
def scale_first(k):
    print("scale", 1)
    g = lambda value: value * k
    print(g(3) + 1)
    return g
def scale_second(k):
    print("scale", 2)
    g = lambda other: other * k
    print(g(3) + 1)
    return g
def bind_first(items):
    print("bind", 1)
    kept = []
    for j in items:
        kept.append(lambda j=j: j * 2)
    return kept
def bind_second(items):
    print("bind", 2)
    kept = []
    for i in items:
        kept.append(lambda i=i: i * 2)
    return kept
def lazy_first(items):
    print("lazy", 1)
    doubled = (item * 2 for item in items)
    print("made")
    return doubled
def lazy_second(items):
    print("lazy", 2)
    doubled = (item * 3 for item in items)
    print("made")
    return doubled
def counter_first(words):
    print("count", 1)
    counts = collections.defaultdict(lambda: 0)
    for word in words:
        counts[word] += 1
    return counts
def counter_second(words):
    print("count", 2)
    counts = collections.defaultdict(lambda: 0)
    for word in words:
        counts[word] += 2
    return counts
def inner_first(k, flag):
    print("inner", 1)
    if flag:
        def step(v):
            return v + k
        result = step
    else:
        result = None
    return result
def inner_second(k, flag):
    print("inner", 2)
    if flag:
        def step(v):
            return v + k
        result = step
    else:
        result = None
    return result
if __name__ == "__main__":
    print(make_first(1).__qualname__, make_second(1).__qualname__)
    print(inspect.signature(scale_first(2)), inspect.signature(scale_second(2)))
    for call in (lambda: scale_first(2)(value=1), lambda: scale_second(2)(other=1)):
        try:
            print(call())
        except TypeError as error:
            print("TypeError", error)
    print([(f.__qualname__, str(inspect.signature(f)), f()) for f in bind_first([1, 2]) + bind_second([3])])
    print(lazy_first([1]).__qualname__, lazy_second([2]).__qualname__)
    print(counter_first(["a", "a"]).default_factory.__qualname__, dict(counter_second(["b"])))
    print(inner_first(1, True).__qualname__, inner_second(2, True)(5))
