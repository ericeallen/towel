# Round-3 audit case misc_binder_renamed_del_message (misc: renamed-binder-deleted-then-read).
# P1-5: item and elem are one binder renamed between the sites; the shared helper's
# UnboundLocalError names item at both.
def f1(xs, flag):
    item = xs[0]
    if flag:
        del item
    r = item * 2
    print("f1", r)
    return r


def f2(ys, flag):
    elem = ys[0]
    if flag:
        del elem
    r = elem * 2
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
    _calls('pkg_m.f1', pkg_m.f1, [([1], False), ([1], True)])
    _calls('pkg_m.f2', pkg_m.f2, [([1], False), ([1], True)])
