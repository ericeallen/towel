# Round-3 audit case ctrl_nested_try (ctrl: nested-try-reraise), whose behaviour the audit found
# kept.
import contextlib


def f1(d):
    try:
        try:
            v = d["a"]
        except KeyError:
            print("inner miss")
            raise ValueError("no a")
        print("have", v)
    except ValueError as e:
        print("outer", e)
        v = -1
    return v


def f2(d):
    try:
        try:
            v = d["a"]
        except KeyError:
            print("inner miss")
            raise ValueError("no a")
        print("have", v)
    except ValueError as e:
        print("outer", e)
        v = -2
    return v


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
    _calls('pkg_m.f1', pkg_m.f1, [({'a': 1},), ({},)])
    _calls('pkg_m.f2', pkg_m.f2, [({'a': 1},), ({},)])
