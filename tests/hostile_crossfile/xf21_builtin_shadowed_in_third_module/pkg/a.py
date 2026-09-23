def fa(values):
    print("pre", "a")
    size = len(values) if hasattr(values, "__len__") else values
    size = size * 2
    print("post", size)
    return size
