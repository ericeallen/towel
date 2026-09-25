# A namesake of the zzlib library, sharing one module name with it; it never ships.
def echo(message):
    print("local echo", message)


def shared(n):
    print("local")
    total = n * 3
    extra = total + 7
    print("shared", total, extra)
    return extra
