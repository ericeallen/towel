# A `type` statement's value and a type parameter's bound are evaluated when
# first read, from the variable's binding at that moment: closures, like a
# lambda's body. Moved into a helper, an alias made in the block keeps the
# helper's n while the caller rebinds its own afterwards, and a generic class
# made before the block keeps the caller's n while the block's loop rebinds
# the helper's. (Python 3.12 syntax: skipped where it does not parse.)
def alias_first(n):
    print("alias", 1)
    type Alias = list[n]
    kept = [Alias, "one"]
    print("mid", kept[1])
    n = "changed"
    return kept[0].__value__
def alias_second(n):
    print("alias", 2)
    type Alias = list[n]
    kept = [Alias, "two"]
    print("mid", kept[1])
    n = "changed"
    return kept[0].__value__
def bound_first(items):
    n = None
    class Box[T: n]:
        pass
    for n in items:
        print("item", n)
    print("done", "one")
    return Box.__type_params__[0].__bound__
def bound_second(items):
    n = None
    class Box[T: n]:
        pass
    for n in items:
        print("item", n)
    print("done", "two")
    return Box.__type_params__[0].__bound__
if __name__ == "__main__":
    print(alias_first(int), alias_second(str))
    print(bound_first([int, str]), bound_second([float]))
