# ``limit`` is module data a callback rebinds between the block's two reads.
# A helper that read it once, at the call, would use the old value for the
# second read; a bare module reference inside the helper reads it when the
# block did.
limit = 1
def bump():
    global limit
    limit += 10
    return 0
def first(items):
    items = list(items)
    total = len(items) + limit
    total += bump()
    total += limit
    return total
def second(items):
    total = len(items) + limit
    total += bump()
    total += limit
    return total * 2
if __name__ == "__main__":
    print(first([1]), second([1, 2]), limit)
