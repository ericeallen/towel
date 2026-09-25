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

# Round-3 audit case cl_local_K (cluster: third-site-local-shadows-global), whose behaviour the
# audit found kept.
K = 10


def f1(xs):
    t = 0
    for x in xs:
        t += x * K
    print("f1", t)
    return t


def f2(xs):
    t = 0
    for x in xs:
        t += x * K
    print("f2", t)
    return t


def f3(xs):
    K = 20
    t = 0
    for x in xs:
        t += x * K
    print("f3", t)
    return t


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
    _calls('pkg_m.f3', pkg_m.f3, [([1, 2],), ([],)])
    def _probe_4():
        pkg_m.K = 3
        __result__ = (pkg_m.f1([1]), pkg_m.f3([1]))
        return __result__
    _value('probe 4', _probe_4)
