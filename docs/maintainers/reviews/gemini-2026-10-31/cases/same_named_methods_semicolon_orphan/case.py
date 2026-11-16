class First:
    def compute(self, x):
        a = x + 1
        b = a * 2
        c = b + 3; res = c + 10
        c = 0
        return res


class Second:
    def compute(self, x):
        a = x + 1
        b = a * 2
        c = b + 3; res = c * 2
        c = 0
        return res + 1
