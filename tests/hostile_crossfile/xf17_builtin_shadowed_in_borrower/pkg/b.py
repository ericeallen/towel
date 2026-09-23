# ``len`` is this module's own function; a.py reads the builtin. A helper
# hosted in a.py that read ``len`` bare would measure with the builtin for
# this module's call too, so the name must come from each caller, including
# the reads inside the lambda and the comprehension.
def len(item):
    return 99


def fb(values):
    print("pre", "b")
    measure = lambda item: len(item)
    sizes = [len(str(v)) for v in values] + [measure(values)]
    sizes = sizes * 2
    print("post", sizes)
    return sizes
