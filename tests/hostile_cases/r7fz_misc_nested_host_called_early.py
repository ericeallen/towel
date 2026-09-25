# Round-3 audit case misc_nested_host_called_early (misc: helper-in-common-function-called-early),
# whose behaviour the audit found kept.
def outer(xs):
    def a():
        t = 0
        for x in xs:
            t += x * 2
        print("a", t)
        return t

    first = a()

    def b():
        t = 0
        for x in xs:
            t += x * 2
        print("b", t)
        return t + 1

    return first, b()


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
    _calls('pkg_m.outer', pkg_m.outer, [([1, 2],)])
