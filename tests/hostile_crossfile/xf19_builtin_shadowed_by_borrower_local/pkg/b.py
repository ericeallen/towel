# ``len`` is a local of fb; the same block in a.py reads the builtin.
def fb(values, len=lambda item: 40):
    print("pre", "b")
    size = len(values) + 1
    size = size * 2
    print("post", size)
    return size
