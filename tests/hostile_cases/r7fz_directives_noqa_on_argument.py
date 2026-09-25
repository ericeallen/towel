# Round-3 audit case dir_noqa_on_argument (directives: noqa-on-differing-expr), whose behaviour
# the audit found kept.
def f1(n, d):
    t = n + 1
    w = t * 2 + d["a"]  # noqa: E501
    print("f1", t, w, "padding padding padding padding padding padding padding padding")
    return w


def f2(n, d):
    t = n + 1
    w = t * 2 + d["b"]  # noqa: E501
    print("f2", t, w, "padding padding padding padding padding padding padding padding")
    return w


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
    _calls('pkg_m.f1', pkg_m.f1, [(1, {'a': 1, 'b': 2})])
    _calls('pkg_m.f2', pkg_m.f2, [(1, {'a': 1, 'b': 2})])
