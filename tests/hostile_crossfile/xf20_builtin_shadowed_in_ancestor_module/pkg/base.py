# The shared ancestor's module binds ``len`` to its own function. A method
# helper hosted in Base that read ``len`` bare would call this one, where
# both subclasses in servers.py call the builtin.
def len(item):
    return 99


class Base:
    def describe(self):
        return type(self).__name__
