import re
def w1(s):
    parts = []
    parts.append("x")
    if (m := re.match(r"(\d+)", s)):
        parts.append(m.group(1))
    parts.append("y")
    return parts, m
def w2(s):
    parts = []
    parts.append("x")
    if (m := re.match(r"(\d+)", s)):
        parts.append(m.group(1) * 2)
    parts.append("y")
    return parts, m
if __name__ == "__main__":
    print(w1("12a"), w1("zz"), w2("3"))
