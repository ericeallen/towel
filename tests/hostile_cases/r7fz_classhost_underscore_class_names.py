# Round-3 audit case cls_underscore_class_names (classhost: mangling-strips-underscores), whose
# behaviour the audit found kept.
class _Foo:
    def __init__(self):
        self.base = 1
        self.log = []

    def m1(self, n):
        t = self.base + n
        u = t * 2
        self.log.append(u)
        print("m1", t, u)
        return u

    def m2(self, n):
        t = self.base + n
        u = t * 2
        self.log.append(u)
        print("m2", t, u)
        return u + 1


class Foo(_Foo):
    def n1(self, n):
        s = [self.base] * n
        w = len(s) + 10
        self.log.append(("n", w))
        print("n1", s, w)
        return w

    def n2(self, n):
        s = [self.base] * n
        w = len(s) + 10
        self.log.append(("n", w))
        print("n2", s, w)
        return w * 3


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
    _value('pkg_m._Foo().m1(1)', lambda: pkg_m._Foo().m1(1))
    _value('pkg_m.Foo().m1(1)', lambda: pkg_m.Foo().m1(1))
    _value('pkg_m.Foo().m2(2)', lambda: pkg_m.Foo().m2(2))
    _value('pkg_m.Foo().n1(2)', lambda: pkg_m.Foo().n1(2))
    _value('pkg_m.Foo().n2(2)', lambda: pkg_m.Foo().n2(2))
