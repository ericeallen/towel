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

# The nested f and g of benign and of hazard are the same code, but hazard's
# cb rebinds the x they read after calling it: passed to a helper, x would be
# read before cb runs. A verdict that nothing rebinds x, memoized for the
# benign pair, once answered for the hazardous one too (round-3 audit, P1-1).
# The benign pair shares a helper; hazard's keeps its code.
def benign(cb):
    x = 0

    def f():
        print("start", x)
        cb()
        print("after", x)
        print("end")

    def g():
        print("start", x)
        cb()
        print("after", x)
        print("end")

    f()
    g()


def hazard():
    x = 0

    def cb():
        nonlocal x
        x += 1

    def f():
        print("start", x)
        cb()
        print("after", x)
        print("end")

    def g():
        print("start", x)
        cb()
        print("after", x)
        print("end")

    f()
    g()


if __name__ == "__main__":
    benign(lambda: None)
    hazard()
