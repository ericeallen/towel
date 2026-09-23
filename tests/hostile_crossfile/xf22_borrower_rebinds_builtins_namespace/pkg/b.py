# Functions defined after this line look builtins up in this dictionary,
# where ``len`` is not the builtin; no statement here binds ``len`` itself.
import builtins

__builtins__ = dict(vars(builtins), len=lambda item: 7)


def fb(values):
    print("pre", "b")
    size = len(values) + 1
    size = size * 2
    print("post", size)
    return size
