# Two subclasses of an ancestor defined in another module build the same
# state from ``Token``, a name only this module imports. A method helper
# hosted in the ancestor would read ``Token`` bare in base.py and raise
# NameError; it must take ``Token`` as a parameter there.
from .base import Base
from .tokens import Token


class Small(Base):
    def __init__(self, size):
        self.token = Token("small", size)
        self.limit = size * 2
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "s"


class Large(Base):
    def __init__(self, size):
        self.token = Token("small", size)
        self.limit = size * 2
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "l"
