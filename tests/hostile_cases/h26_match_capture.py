def m1(cmd):
    match cmd:
        case ["go", direction]:
            dest = direction
        case _:
            dest = None
    print("m1", dest)
    return dest
def m2(cmd):
    match cmd:
        case ["go", direction]:
            dest = direction
        case _:
            dest = None
    print("m2", dest)
    return dest
if __name__ == "__main__":
    print(m1(["go", "n"]), m1([]), m2(["go", "s"]))
