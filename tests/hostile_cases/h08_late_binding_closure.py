def l1(items):
    def show():
        return factor
    out = [show for _ in items]
    out.append("x")
    factor = 10
    return [o() if callable(o) else o for o in out]
def l2(items):
    def show():
        return factor
    out = [show for _ in items]
    out.append("y")
    factor = 20
    return [o() if callable(o) else o for o in out]
if __name__ == "__main__":
    print(l1([1, 2]), l2([1]))
