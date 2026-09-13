def _extracted_func_0(x):
    return "user-defined"
def __extracted_func_1(x):
    return "user-defined-2"
def u1(items):
    out = []
    for i in items:
        out.append(i * 3)
    out.append(_extracted_func_0(1))
    return out
def u2(items):
    out = []
    for i in items:
        out.append(i * 3)
    out.append(__extracted_func_1(1))
    return out
if __name__ == "__main__":
    print(u1([1]), u2([2]))
