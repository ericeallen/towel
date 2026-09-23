# Both methods import pkg.y.settings relatively. A method helper hosted
# in Base, in pkg.x, would import pkg.x.settings instead.
from ..x.base import Base


class Small(Base):
    def __init__(self, size):
        from .settings import LIMIT
        self.limit = size * LIMIT
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "s"


class Large(Base):
    def __init__(self, size):
        from .settings import LIMIT
        self.limit = size * LIMIT
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "l"
