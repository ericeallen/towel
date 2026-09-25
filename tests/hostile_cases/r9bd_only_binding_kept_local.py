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

# Controls, each transformed. In summarize_*, the block holds the only
# binding of total and the function reads total before and after it; the
# call assigns total from the helper, so total stays local and the early read
# still raises UnboundLocalError. In spread_*, the block's for is the only
# binding of x, and the comprehension before it binds an x of its own, so no
# read outside the block depends on the function's x.
total = "module total"


def summarize_1(n):
    if n < 0:
        return ("early", total)
    total = n + 1
    print("total", total)
    print("n", n)
    return ("late", total)


def summarize_2(q):
    if q < 0:
        return ("early", total)
    total = q + 1
    print("total", total)
    print("n", q)
    return ("late", total)


def spread_1(n):
    ys = [x * 2 for x in range(n)]
    for x in ys:
        print("x", x)
        print("n", n)
    print("spread", len(ys))
    return ys


def spread_2(q):
    ys = [x + 7 for x in range(q) if x]
    for x in ys:
        print("x", x)
        print("n", q)
    print("spread", len(ys))
    return ys


if __name__ == "__main__":
    for function, argument in (
        (summarize_1, 2),
        (summarize_1, -1),
        (summarize_2, 3),
        (summarize_2, -1),
        (spread_1, 3),
        (spread_2, 3),
    ):
        try:
            print(function.__name__, argument, "->", repr(function(argument)))
        except Exception as error:
            print(function.__name__, argument, "raised", type(error).__name__, error)
