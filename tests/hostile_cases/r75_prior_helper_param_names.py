def __extracted_func_0(__param_0, items):
    out = []
    for i in items:
        out.append(i + __param_0)
    out.append(len(items))
    return out
def __extracted_func_1(__param_0, items):
    out = []
    for i in items:
        out.append(i + __param_0)
    out.append(len(items) * 2)
    return out
if __name__ == "__main__":
    print(__extracted_func_0(1, [1, 2]), __extracted_func_1(2, [3]))
