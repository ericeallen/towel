print('importing pkg.w')


class Widget:
    def __init__(self) -> None:
        self.size = 3

    def poke(self, k: int) -> None:
        print('poke', k)
