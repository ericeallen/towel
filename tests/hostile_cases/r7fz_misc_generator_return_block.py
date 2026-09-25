# Round-3 audit case misc_generator_return_block (misc: generator-return-value-in-block), whose
# behaviour the audit found kept.
def f1(xs):
    yield 0
    t = 0
    for x in xs:
        t += x * 2
    print("f1", t)
    return t


def f2(xs):
    yield 1
    t = 0
    for x in xs:
        t += x * 2
    print("f2", t)
    return t


def drive(g):
    out = []
    try:
        while True:
            out.append(next(g))
    except StopIteration as e:
        out.append(("stop", e.value))
    return out


if __name__ == "__main__":
    import sys

    import copy
    import re

    def _shown(value):
        return re.sub(r"0x[0-9a-fA-F]+", "0xADDR", repr(value))

    def _calls(label, function, argument_sets):
        for arguments in argument_sets:
            arguments = copy.deepcopy(arguments)
            try:
                outcome = "-> " + _shown(function(*arguments))
            except Exception as error:
                outcome = f"raised {type(error).__name__}: {_shown(str(error))}"
            print(label, outcome, "| arguments after:", _shown(arguments))

    def _value(label, produce):
        try:
            print(label, "=", _shown(produce()))
        except Exception as error:
            print(label, "raised", type(error).__name__, _shown(str(error)))

    pkg_m = sys.modules[__name__]
    _value('pkg_m.drive(pkg_m.f1([1, 2]))', lambda: pkg_m.drive(pkg_m.f1([1, 2])))
    _value('pkg_m.drive(pkg_m.f2([1, 2]))', lambda: pkg_m.drive(pkg_m.f2([1, 2])))
