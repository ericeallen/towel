# Round-3 audit case pre_def_conditional_rebind (prebound: funcdef-rebind). P1-1: a def inside an
# if rebinds g, bound before the block; with the flag false the helper's g is unbound.
def f1(flag):
    def g():
        return "outer1"
    print("start", flag)
    if flag:
        def g():
            return "inner"
    r = g()
    print("f1", r)
    return r


def f2(flag):
    def g():
        return "outer2"
    print("start", flag)
    if flag:
        def g():
            return "inner"
    r = g()
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
