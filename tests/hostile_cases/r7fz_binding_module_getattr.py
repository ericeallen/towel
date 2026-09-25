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

# Round-3 audit case bind_module_getattr (binding: module-getattr), whose behaviour the audit
# found kept.
def __getattr__(name):
    print("module getattr", name)
    return 7


def f1(xs):
    n = len(xs)
    try:
        v = UNDEFINED_THING
    except NameError as e:
        v = str(e)
    print("f1", n, v)
    return n


def f2(ys):
    n = len(ys) + 1
    try:
        v = UNDEFINED_THING
    except NameError as e:
        v = str(e)
    print("f2", n, v)
    return n


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
    _calls('pkg_m.f1', pkg_m.f1, [([1, 2],)])
    _calls('pkg_m.f2', pkg_m.f2, [([1],)])
    _value('pkg_m.UNDEFINED_THING', lambda: pkg_m.UNDEFINED_THING)
