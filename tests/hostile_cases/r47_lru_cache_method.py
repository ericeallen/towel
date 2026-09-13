import functools
class C:
    def __init__(self, k): self.k = k
    @functools.lru_cache(maxsize=None)
    def m1(self, n):
        total = 0
        for i in range(n):
            total += i * self.k
        total += 1
        return total
    @functools.lru_cache(maxsize=None)
    def m2(self, n):
        total = 0
        for i in range(n):
            total += i * self.k
        total += 2
        return total
if __name__ == "__main__":
    c = C(3); print(c.m1(4), c.m2(4), c.m1(4), C.m1.cache_info().hits)
