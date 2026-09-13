class U:
    def __init__(self): self.name = "n"; self.mail = "m"
    @property
    def loud(self):
        print("loud-accessed"); return self.name.upper()
def f1(u):
    print("start")
    parts = []
    parts.append(f"<{u.loud}>")
    parts.append(f"<{u.loud}>")
    return parts
def f2(u):
    print("start")
    parts = []
    parts.append(f"<{u.mail}>")
    parts.append(f"<{u.mail}>")
    return parts
if __name__ == "__main__":
    print(f1(U()), f2(U()))
