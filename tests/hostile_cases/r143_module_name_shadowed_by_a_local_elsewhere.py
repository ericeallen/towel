# ``scale`` is a module name at two sites and a local at a third that has the
# same block shape. The helper reads the module's ``scale`` bare, so the third
# site must not join it: its own ``scale`` is a different binding.
scale = 10
def a(items):
    items = list(items)
    total = sum(items)
    total *= scale
    total += 1
    return total
def b(items):
    total = sum(items)
    total *= scale
    total += 1
    return total - 1
def c(items, scale):
    total = sum(items)
    total *= scale
    total += 1
    return total + 100
if __name__ == "__main__":
    print(a([1, 2]), b([3]), c([4], 2))
