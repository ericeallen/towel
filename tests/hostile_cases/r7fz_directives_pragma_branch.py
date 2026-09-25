# Round-3 audit case dir_pragma_branch (directives: pragma-no-cover-branch), whose behaviour the
# audit found kept.
def f1(xs):
    t = 0
    for x in xs:
        t += x
    if t < -1000:  # pragma: no cover
        print("impossible")
        t = 0
    print("f1", t)
    return t + 1


def f2(xs):
    t = 0
    for x in xs:
        t += x
    if t < -1000:  # pragma: no cover
        print("impossible")
        t = 0
    print("f2", t)
    return t + 2


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
    _calls('pkg_m.f1', pkg_m.f1, [([1, 2],)])
    _calls('pkg_m.f2', pkg_m.f2, [([1, 2],)])
