# Round-3 audit case bi_x_shadow_in_b (builtins: cross-borrower-shadows), whose behaviour the
# audit found kept.
if __name__ == "__main__":
    import pkg.a as pkg_a
    import pkg.b as pkg_b
    import pkg
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

    _calls('pkg_a.fa', pkg_a.fa, [([1, 2],)])
    _calls('pkg_b.fb', pkg_b.fb, [([1, 2],)])
