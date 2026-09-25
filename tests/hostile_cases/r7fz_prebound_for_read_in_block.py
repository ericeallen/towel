# Round-3 audit case pre_for_read_in_block (prebound: for-target). P1-1: i is bound before the
# block and rebound by its for target; with an empty list the helper reads i unbound instead of
# the value before the loop.
def f1(xs):
    i = -1
    for i in xs:
        print("it", i)
    last = i * 2
    print("f1", last)
    return last


def f2(xs):
    i = -2
    for i in xs:
        print("it", i)
    last = i * 2
    print("f2", last)
    return last


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
