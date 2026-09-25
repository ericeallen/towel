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

# Round-3 audit case ex_typed_thunk_lambda_default_pyright (extra: typed-leading-thunk-pyright).
# P1-3 in the typed mode, pyright strict: the lambda default's effect moves behind the eagerly
# passed thunk, and pyright accepts it.
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

    _value('pkg_m.f1(pkg_m.Loud(3))', lambda: pkg_m.f1(pkg_m.Loud(3)))
    _value('pkg_m.f2(pkg_m.Loud(3))', lambda: pkg_m.f2(pkg_m.Loud(3)))
