def lit1(x):
    vals = ["\N{BULLET}", "\x00\t ", r"\d+\n", b"\xff\x00", "'\"", '"\'']
    vals += [1_000, 0x_FF, 0o17, 0b1010, 1e-9, 1e400, -0.0, 1j, 2.5e3j, 123456789012345678901234567890]
    vals += ["""tri
ple""", '''sin
gle''', "\\", "tab\tend", "\a\b\f\v\r"]
    vals.append(x)
    return vals
def lit2(x):
    vals = ["\N{BULLET}", "\x00\t ", r"\d+\n", b"\xff\x00", "'\"", '"\'']
    vals += [1_000, 0x_FF, 0o17, 0b1010, 1e-9, 1e400, -0.0, 1j, 2.5e3j, 123456789012345678901234567890]
    vals += ["""tri
ple""", '''sin
gle''', "\\", "tab\tend", "\a\b\f\v\r"]
    vals.append(x)
    return vals + [2]
if __name__ == "__main__":
    print(repr(lit1(1)), repr(lit2(2)))
