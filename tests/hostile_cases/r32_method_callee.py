class T:
    def __init__(self): self.calls = []
    def strip(self, s): self.calls.append("strip"); return s.strip()
    def upper(self, s): self.calls.append("upper"); return s.upper()
def m1(t, s):
    parts = []
    parts.append(t.strip(s))
    parts.append(len(parts))
    parts.append(list(t.calls))
    return parts
def m2(t, s):
    parts = []
    parts.append(t.upper(s))
    parts.append(len(parts))
    parts.append(list(t.calls))
    return parts
if __name__ == "__main__":
    print(m1(T(), " a "), m2(T(), " b "))
