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

# A directive governs its whole line. Each block here starts after, or ends
# before, a statement that stays on a line carrying a directive, so splicing
# the call in would part the directive from that statement: declined.
log = []
def f1(n):
    a = n + 1; b = a * 2  # noqa: E702
    print("f1", a, b)
    return a + b
def f2(n):
    a: int = n + 2; b = a * 2  # noqa: E702
    print("f2", a, b)
    return a + b
def g1(items):
    total = sum(items)
    print("g1 total", total)
    count = len(items); log.append(count)  # type: ignore[attr-defined]
    return total + count
def g2(items):
    total = sum(items)
    print("g2 total", total)
    count = len(items); scaled = count * 3  # type: ignore[attr-defined]
    return total + scaled
if __name__ == "__main__":
    print(f1(1), f2(1), g1([1, 2]), g2([3, 4, 5]), log)
