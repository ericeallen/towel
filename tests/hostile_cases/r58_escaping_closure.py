def e1(base):
    scale = base * 2
    def f(v):
        return v * scale
    result = [f(1), f(2)]
    scale = 0
    return result, f(3)
def e2(base):
    scale = base * 3
    def f(v):
        return v * scale
    result = [f(1), f(2)]
    scale = 0
    return result, f(3)
if __name__ == "__main__":
    print(e1(1), e2(1))
