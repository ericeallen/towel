# Round-3 audit case bind_dunder_name_file_samemodule (binding: dunder-module-names), whose
# behaviour the audit found kept.
import os


def f1(xs):
    n = len(xs)
    where = __name__ + ":" + os.path.basename(__file__)
    print("f1", n, where, __doc__)
    return n


def f2(ys):
    n = len(ys) + 1
    where = __name__ + ":" + os.path.basename(__file__)
    print("f2", n, where, __doc__)
    return n


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
    _calls('pkg_m.f2', pkg_m.f2, [([1],)])
