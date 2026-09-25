# Round-3 audit case cls_enum_methods (classhost: enum), whose behaviour the audit found kept.
import enum


class Color(enum.Enum):
    RED = 1
    GREEN = 2

    def m1(self, n):
        t = self.value + n
        u = t * 2
        print("m1", self.name, t, u)
        return u

    def m2(self, n):
        t = self.value + n
        u = t * 2
        print("m2", self.name, t, u)
        return u + 1


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
    _value('pkg_m.Color.RED.m1(1)', lambda: pkg_m.Color.RED.m1(1))
    _value('pkg_m.Color.GREEN.m2(1)', lambda: pkg_m.Color.GREEN.m2(1))
    _value('list(pkg_m.Color)', lambda: list(pkg_m.Color))
    _value('list(pkg_m.Color.__members__)', lambda: list(pkg_m.Color.__members__))
