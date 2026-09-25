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

# Each block ends at the first statement of a line: the call must keep the
# statement after it, which differs between the sites.
log = []
def g1(items):
    total = sum(items)
    print("g1 total", total)
    count = len(items); log.append(("g1", count))
    return total * 10 + count
def g2(items):
    total = sum(items)
    print("g2 total", total)
    count = len(items); scaled = count * 3
    return total + scaled
if __name__ == "__main__":
    print(g1([1, 2]), g2([3, 4, 5]), log)
