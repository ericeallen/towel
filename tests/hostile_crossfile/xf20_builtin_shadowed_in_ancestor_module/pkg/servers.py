from .base import Base


class Small(Base):
    def __init__(self, items):
        self.count = len(items)
        self.limit = self.count * 2
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "s"


class Large(Base):
    def __init__(self, items):
        self.count = len(items)
        self.limit = self.count * 2
        self.label = self.describe() + ":" + str(self.limit)
        self.kind = "l"
