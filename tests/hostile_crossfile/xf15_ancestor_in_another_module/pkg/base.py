# The shared ancestor lives in its own module, which does not import Token.
class Base:
    def describe(self):
        return type(self).__name__
