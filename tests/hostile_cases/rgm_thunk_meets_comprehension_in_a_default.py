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
# Gemini review of 2026-10-31, case inlined_comp_in_default_thunk_cell, in the
# shape that still reproduced on 1.947: a comprehension in a nested function's
# default runs in f1's own frame (PEP 709), so on Python 3.12 and later its
# target k empties the cell a lambda of f1 would read outer's k from. A thunk
# lambda: k handed to a helper raised NameError where the block printed 105.
def outer(items, flag):
    k = 100

    def f1(x):
        def helper(cached=[k * 2 for k in items]):
            return sum(cached)
        print("total", helper())
        if flag:
            print("value", k + x)
        print("done", x)

    def f2(x):
        print("total", len(items))
        if flag:
            print("value", (x + 1) + x)
        print("done", x)

    f1(5)
    f2(5)


if __name__ == "__main__":
    for flag in (True, False):
        try:
            outer([1, 2, 3], flag)
        except Exception as error:
            print("raised", type(error).__name__, error)
