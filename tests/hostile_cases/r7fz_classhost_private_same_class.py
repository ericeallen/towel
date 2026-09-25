# Round-3 audit case cls_private_same_class (classhost: private-names), whose behaviour the audit
# found kept.
class A:
    def __init__(self):
        self.__cache = {}
        self.hits = 0

    def m1(self, k):
        v = self.__cache.get(k)
        if v is None:
            v = k * 2
            self.__cache[k] = v
        self.hits += 1
        print("m1", k, v, self.hits)
        return v

    def m2(self, k):
        v = self.__cache.get(k)
        if v is None:
            v = k * 3
            self.__cache[k] = v
        self.hits += 1
        print("m2", k, v, self.hits)
        return v


class B(A):
    def _A__extracted_func_0(self, *a, **k):
        print("B's own mangled name")
        return "B"


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
    def _probe_1():
        a = pkg_m.A()
        __result__ = (a.m1(1), a.m1(1), a.m2(2), a.hits)
        return __result__
    _value('probe 1', _probe_1)
    def _probe_2():
        b = pkg_m.B()
        __result__ = (b.m1(1), b.m2(2), b.hits)
        return __result__
    _value('probe 2', _probe_2)
