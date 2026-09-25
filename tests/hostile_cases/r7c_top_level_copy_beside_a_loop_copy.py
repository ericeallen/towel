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

# As r7c_loop_copy_beside_a_top_level_twin, but g returns w as well, so the
# top-level copy and g's share a helper returning it. The loop's copy keeps its
# code.
def f(items):
    w = len(items)
    print("w", w)
    print("x")
    for _ in range(3):
        print("before", w)
        items.append(0)
        w = len(items)
        print("w", w)
        print("x")


def g(items):
    w = len(items)
    print("w", w)
    print("x")
    return w


if __name__ == "__main__":
    f([1, 2])
    print(g([1]))
