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

# The round-4 audit's P1-04 reproducer. Each block holds its function's only
# binding of a name (total, count) that the function also reads before the
# block: on the early return, and in a try whose handler catches the
# UnboundLocalError. Moved into a helper, the block took the name's locality
# with it, and both reads found the module's name instead: ('empty', 'module
# total') where the original raised UnboundLocalError.
total = "module total"


def summarize_a(items):
    if not items:
        return ("empty", total)
    total = sum(items)
    total = total * 2
    print("doubled", total)
    return ("ok", len(items))


def summarize_b(rows):
    if not rows:
        return ("empty", total)
    total = sum(rows)
    total = total * 2
    print("doubled", total)
    return ("ok", len(rows))


def report_a(values):
    try:
        seen = count
    except UnboundLocalError as error:
        seen = str(error)
    count = len(values)
    count += 1
    print("count", count)
    return seen


def report_b(values):
    try:
        seen = count
    except UnboundLocalError as error:
        seen = str(error)
    count = len(values)
    count += 1
    print("count", count)
    return seen


count = "module count"
if __name__ == "__main__":
    for function, argument in (
        (summarize_a, [1, 2]),
        (summarize_a, []),
        (summarize_b, []),
        (report_a, [1]),
        (report_b, [1, 2]),
    ):
        try:
            print(function.__name__, argument, "->", repr(function(argument)))
        except Exception as error:
            print(function.__name__, argument, "raised", type(error).__name__, error)
