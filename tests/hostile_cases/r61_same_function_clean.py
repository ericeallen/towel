def f(items, limit):
    out = []
    for i in items:
        if i > limit:
            out.append(i * 2)
    out.append("mid")
    for i in items:
        if i > limit:
            out.append(i * 2)
    out.append("end")
    return out
if __name__ == "__main__":
    print(f([1, 5, 20], 2))
