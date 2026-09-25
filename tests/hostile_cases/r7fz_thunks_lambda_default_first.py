# Round-3 audit case th_lambda_default_first (thunks: lambda-default-before-thunk), whose
# behaviour the audit found kept.
def tr(tag, v=None):
    print("tr", tag)
    return v


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
    g = lambda k=tr("default", 1): k * 2
    y = tr("A", len(xs))
    z = g() + (y or 0)
    print("f1", y, z)
    return z


def f2(xs):
    g = lambda k=tr("default", 1): k * 2
    y = tr("B", sum(xs))
    z = g() + (y or 0)
    print("f2", y, z)
    return z


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
    _calls('pkg_m.f1', pkg_m.f1, [([1, 2],), ([],)])
    _calls('pkg_m.f2', pkg_m.f2, [([1, 2],), ([],)])
