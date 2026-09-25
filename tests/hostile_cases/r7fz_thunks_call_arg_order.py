# Round-3 audit case th_call_arg_order (thunks: thunk-call-arg-order), whose behaviour the audit
# found kept.
def tr(tag, v=None):
    print("tr", tag)
    return v


def combine(a, b, k):
    return a + b + k


class Loud:
    def __init__(self, v):
        self.v = v

    def __hash__(self):
        print("hash", self.v)
        return hash(self.v)

    def __eq__(self, o):
        print("eq", self.v)
        return isinstance(o, Loud) and o.v == self.v

    def __iter__(self):
        print("iter", self.v)
        return iter([self.v])

    def keys(self):
        print("keys", self.v)
        return ["k"]

    def __getitem__(self, k):
        return self.v

    @property
    def prop(self):
        print("prop", self.v)
        return self.v


def f1(xs):
    r = combine(tr("a1", 1), tr('A', 2), k=tr("kw", 3))
    w = r * 2
    print("f1", w)
    return w


def f2(xs):
    r = combine(tr("a1", 1), tr('B', 2), k=tr("kw", 3))
    w = r * 2
    print("f2", w)
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
    _calls('pkg_m.f1', pkg_m.f1, [([1],)])
    _calls('pkg_m.f2', pkg_m.f2, [([1],)])
