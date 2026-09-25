# Round-3 audit case tg2_import_alias_flag_both (typeguard2: import_alias_flag-both). P1-4, mypy
# and pyright strict: 'from pkg.compat import DEBUG as TYPE_CHECKING' is rebound False by the
# added typing import.
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

    _value("sorted(k for k in sys.modules if k.startswith('pkg'))", lambda: sorted(k for k in sys.modules if k.startswith('pkg')))
    _value("getattr(pkg_m, 'MODE', None)", lambda: getattr(pkg_m, 'MODE', None))
    _value("getattr(pkg_m, 'TYPE_CHECKING', '<unbound>')", lambda: getattr(pkg_m, 'TYPE_CHECKING', '<unbound>'))
    _value('pkg_m.f1(1)', lambda: pkg_m.f1(1))
    _value('pkg_m.f2(1)', lambda: pkg_m.f2(1))
