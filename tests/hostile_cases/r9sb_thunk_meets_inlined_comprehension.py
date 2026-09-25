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

# Found by the round-4 binding forms of the differential grammar (seeds 302,
# 409, 1209 and 1450): from Python 3.12 a list comprehension is compiled into
# its function's frame (PEP 709), and where the function reads a name from an
# enclosing function that such a comprehension rebinds, a lambda of the
# function reading the name finds its cell empty. A thunk handed to the helper
# is such a lambda where the block had none, and raised NameError.
def report_a(rows, scale):
    def inner():
        doubled = [scale * 2 for scale in rows]
        print("doubled", doubled)
        if rows:
            print("first", rows[0] * scale)
        print("scale", scale, len(rows))
        return scale + len(doubled)
    return inner()


def report_b(rows, factor):
    def inner():
        doubled = [factor * 2 for factor in rows]
        print("doubled", doubled)
        if rows:
            print("first", rows[0] * factor)
        print("scale", factor, len(rows))
        return factor + len(doubled)
    return inner()


if __name__ == "__main__":
    print(report_a([1, 2], 3), report_a([], 4))
    print(report_b([5], 6), report_b([], 7))
