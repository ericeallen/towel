def fa(values):
    print("pre", "a")
    measure = lambda item: len(item)
    sizes = [len(str(v)) for v in values] + [measure(values)]
    sizes = sizes * 2
    print("post", sizes)
    return sizes
