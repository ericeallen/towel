# Round-3 audit case bind_global_rebound_by_callee (binding: global-rebound-in-block), whose
# behaviour the audit found kept.
K = 1


def setk(v):
    global K
    K = v
    return v


def f1(xs):
    a = setk(len(xs))
    b = K * 2
    print("f1", a, b)
    return a + b


def f2(ys):
    a = setk(len(ys) + 1)
    b = K * 2
    print("f2", a, b)
    return a + b


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
    _calls('pkg_m.f1', pkg_m.f1, [([1, 2],), ([],)])
    _calls('pkg_m.f2', pkg_m.f2, [([1, 2],), ([],)])
    _value('pkg_m.K', lambda: pkg_m.K)
