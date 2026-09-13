import re
def w1(s):
    out = []
    out.append("start")
    if (m := re.match(r"\d+", s)):
        out.append(m.group(0))
    out.append("end")
    return out, m
def w2(s):
    out = []
    out.append("start")
    if s.isdigit():
        out.append(s)
    out.append("end")
    return out, s
if __name__ == "__main__":
    print(w1("12a"), w1("x"), w2("3"), w2("y"))
