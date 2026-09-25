# Round-3 audit case pre_class_conditional_rebind (prebound: classdef-rebind), whose behaviour the
# audit found kept.
def f1(flag):
    class C:
        tag = "outer1"
    print("start", flag)
    if flag:
        class C:
            tag = "inner"
    r = C.tag
    print("f1", r)
    return r


def f2(flag):
    class C:
        tag = "outer2"
    print("start", flag)
    if flag:
        class C:
            tag = "inner"
    r = C.tag
    print("f2", r)
    return r


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
    _calls('pkg_m.f1', pkg_m.f1, [(True,), (False,)])
    _calls('pkg_m.f2', pkg_m.f2, [(True,), (False,)])
