# Neither module imports the other, and both print at import time: hosting
# a helper in either would make importing one run the other's print.
print("importing b")
def build(xs):
    out = []
    for x in xs:
        out.append(x * 2)
        out.append(x * 3)
    out.append(len(out))
    return out
