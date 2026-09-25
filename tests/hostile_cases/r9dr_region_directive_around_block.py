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

# Round-4 audit P1-09 (repro/P1-09-region-directive-around-block): region
# directives opened before a block and closed after it. The helper would stand
# outside the region, so ruff 0.16.9 reported E501 in it and the formatter
# rewrote the matrix onto one line; both pairs are declined.
def f1(rows):
    out = rows[:1]
    # ruff: disable[E501]
    n = len(rows) + 1
    label = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    print("t", n, label)
    # ruff: enable[E501]
    return out


def f2(rows):
    out = rows[1:]
    # ruff: disable[E501]
    n = len(rows) + 1
    label = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    print("t", n, label)
    # ruff: enable[E501]
    return out


def g1(rows):
    head = rows[:1]
    # fmt: off
    matrix = [
        1,   0,   0,
        0,   1,   0,
        0,   0,   1,
    ]
    total = sum(rows)   *   len(matrix)
    print("m", total)
    # fmt: on
    return head


def g2(rows):
    head = rows[1:]
    # fmt: off
    matrix = [
        1,   0,   0,
        0,   1,   0,
        0,   0,   1,
    ]
    total = sum(rows)   *   len(matrix)
    print("m", total)
    # fmt: on
    return head


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
    for name in ("f1", "f2", "g1", "g2"):
        _calls(f"pkg_m.{name}", getattr(pkg_m, name), [([3, 4, 5],)])
