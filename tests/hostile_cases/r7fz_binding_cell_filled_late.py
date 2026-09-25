# Round-3 audit case bind_cell_filled_late (binding: cell-filled-after-def), whose behaviour the
# audit found kept.
def outer1(xs):
    def inner():
        t = 0
        for x in xs:
            t += x * k
        print("inner1", t)
        return t

    k = 2
    r = inner()
    k = 5
    return r, inner()


def outer2(xs):
    def inner():
        t = 0
        for x in xs:
            t += x * k
        print("inner2", t)
        return t

    k = 3
    r = inner()
    k = 7
    return r, inner()


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
    _calls('pkg_m.outer1', pkg_m.outer1, [([1, 2],)])
    _calls('pkg_m.outer2', pkg_m.outer2, [([1],)])
