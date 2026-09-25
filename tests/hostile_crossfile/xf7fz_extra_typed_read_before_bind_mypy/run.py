# Round-3 audit case ex_typed_read_before_bind_mypy (extra: typed-read-before-binding-mypy). P1-7
# in the typed mode, mypy strict: scale = scale(...) raised UnboundLocalError; the call site now
# passes the module's scale and the call succeeds.
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

    _calls('pkg_m.f1', pkg_m.f1, [([1, 2],)])
    _calls('pkg_m.f2', pkg_m.f2, [([1, 2],)])
