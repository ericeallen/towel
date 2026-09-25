# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Round-3 audit case th2_list_starred (thunks2: starred-display-before-thunk). P1-3: a starred
# list display, which iterates and can raise, precedes the thunk that is passed eagerly.
def tr(tag, v=None):
    print("tr", tag)
    return v


class Loud:
    def __init__(self, v):
        self.v = v

    def alpha(self):
        print("alpha", self.v)
        return self.v

    def beta(self):
        print("beta", self.v)
        return self.v * 2

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

    def __str__(self):
        print("str", self.v)
        return "L"

    @property
    def prop(self):
        print("prop", self.v)
        return self.v


class Ctx:
    def __enter__(self):
        print("enter")

    def __exit__(self, *a):
        print("exit")
        return False


def f1(extra, o):
    items = [*extra]
    y = o.alpha()
    z = len(items) + y
    print("f1", y, z)
    return z


def f2(extra, o):
    items = [*extra]
    y = (o.beta() or 0)
    z = len(items) + y
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
    _calls('pkg_m.f1', pkg_m.f1, [([3], pkg_m.Loud(3)), (pkg_m.Loud(5), pkg_m.Loud(3)), (7, pkg_m.Loud(3))])
    _calls('pkg_m.f2', pkg_m.f2, [([3], pkg_m.Loud(3)), (pkg_m.Loud(5), pkg_m.Loud(3)), (7, pkg_m.Loud(3))])
