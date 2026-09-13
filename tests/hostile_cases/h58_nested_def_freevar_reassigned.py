def z1(items):
    factor = 2
    def scale(v):
        return v * factor
    factor = 3
    return [scale(i) for i in items]
def z2(items):
    factor = 2
    def scale(v):
        return v * factor
    factor = 4
    return [scale(i) for i in items]
if __name__ == "__main__":
    print(z1([1]), z2([1]))
