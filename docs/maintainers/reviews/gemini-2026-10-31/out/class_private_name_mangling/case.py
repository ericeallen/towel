class Alpha:
    def __init__(self):
        self.__secret = 10

    def compute(self, x):
        a = x + 1
        b = a * 2
        c = b + self.__secret
        return c + 1


class Beta:
    def __init__(self):
        self.__secret = 20

    def compute(self, x):
        a = x + 1
        b = a * 2
        c = b + self.__secret
        return c + 2
