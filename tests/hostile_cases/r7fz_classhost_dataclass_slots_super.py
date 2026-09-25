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

# Round-3 audit case cls_dataclass_slots_super (classhost: dataclass-slots-super), whose behaviour
# the audit found kept.
from dataclasses import dataclass


class Base:
    def val(self, n):
        return n + 100


@dataclass(slots=True)
class A(Base):
    base: int = 1

    def m1(self, n):
        t = self.base + n
        r = super().val(t)
        print("m1", t, r)
        return r

    def m2(self, n):
        t = self.base + n
        r = super().val(t)
        print("m2", t, r)
        return r * 2


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
        try:
            __result__ = pkg_m.A().m1(1)
        except TypeError as e:
            __result__ = ('TypeError', str(e))
        return __result__
    _value('probe 1', _probe_1)
    def _probe_2():
        try:
            __result__ = pkg_m.A().m2(1)
        except TypeError as e:
            __result__ = ('TypeError', str(e))
        return __result__
    _value('probe 2', _probe_2)
    _value('pkg_m.A.__slots__', lambda: pkg_m.A.__slots__)
