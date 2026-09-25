# Round-3 audit case ex_typed_thunk_lambda_default_mypy (extra: typed-leading-thunk-mypy). P1-3 in
# the typed mode, mypy strict: the lambda default's effect moves behind the eagerly passed thunk,
# and mypy accepts it.
if __name__ == "__main__":
    import pkg.m as pkg_m
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

    _value('pkg_m.f1(pkg_m.Loud(3))', lambda: pkg_m.f1(pkg_m.Loud(3)))
    _value('pkg_m.f2(pkg_m.Loud(3))', lambda: pkg_m.f2(pkg_m.Loud(3)))
