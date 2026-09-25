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

# Round-4 audit, beside P1-09: Towel's own rendering rewrote the layout that
# Black's off and skip directives keep, even when the directive moved with
# the block. The kept statements are now written into the helper as the
# sites wrote them. (This comment does not spell the directives: yapf reads
# them anywhere in a comment.)
def g1(rows):
    head = rows[:1]
    # fmt: off
    matrix = [
        1,   0,   0,
        0,   1,   0,
        0,   0,   1,
    ]
    total = sum(rows)   *   len(matrix)
    # fmt: on
    print("m", total)
    return head


def g2(rows):
    head = rows[:1]
    # fmt: off
    matrix = [
        1,   0,   0,
        0,   1,   0,
        0,   0,   1,
    ]
    total = sum(rows)   *   len(matrix)
    # fmt: on
    print("n", total)
    return head


def h1(rows):
    count = len(rows)
    for row in rows:
        count += row  *  2  # fmt: skip
    print("h", count)
    return count + 1


def h2(rows):
    count = len(rows)
    for row in rows:
        count += row  *  2  # fmt: skip
    print("h", count)
    return count - 1


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
    for name in ("g1", "g2", "h1", "h2"):
        _calls(f"pkg_m.{name}", getattr(pkg_m, name), [([3, 4, 5],)])
