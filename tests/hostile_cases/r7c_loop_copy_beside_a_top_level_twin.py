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

# The loop's copy binds w, which the next iteration reads before the copy runs
# again, so it cannot move without returning w. The top-level copy, the same
# code, is judged first; its bindings and verdicts once stood for the loop's.
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


if __name__ == "__main__":
    f([1, 2])
    g([1])
