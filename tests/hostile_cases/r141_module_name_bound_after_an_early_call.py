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

# ``helper`` is bound after ``main`` has already run once at import time, on
# a path that never read it. Passing ``helper`` eagerly at that call would
# raise NameError before the block could decide not to read it.
def main(items, flag):
    total = 0
    for item in items:
        total += item
    if flag:
        total = helper(total)
    print('main', total)
    return total
def other(items, flag):
    total = 0
    for item in items:
        total += item
    if flag:
        total = helper(total)
    print('other', total)
    return total * 2
EARLY = main([1, 2], False)
def helper(value):
    return value + 100
if __name__ == "__main__":
    print(EARLY, main([3], True), other([4], True), other([5], False))
