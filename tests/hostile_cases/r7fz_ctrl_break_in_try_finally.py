# Round-3 audit case ctrl_break_in_try_finally (ctrl: break-inside-try-finally), whose behaviour
# the audit found kept.
import contextlib


def f1(xs):
    found = None
    for x in xs:
        try:
            if x > 1:
                found = x
                break
        finally:
            print("checked", x)
    print("f1", found)
    return found


def f2(xs):
    found = None
    for x in xs:
        try:
            if x > 2:
                found = x
                break
        finally:
            print("checked", x)
    print("f2", found)
    return found


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
    _calls('pkg_m.f1', pkg_m.f1, [([1, 2, 3],), ([],)])
    _calls('pkg_m.f2', pkg_m.f2, [([1, 2, 3],), ([],)])
