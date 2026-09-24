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

def c1(items):
    lo = hi = items[0]
    for i in items:
        lo = min(lo, i)
        hi = max(hi, i)
    return lo, hi, "c1"
def c2(items):
    lo = hi = items[0]
    for i in items:
        lo = min(lo, i)
        hi = max(hi, i)
    return lo, hi, "c2"
if __name__ == "__main__":
    print(c1([3, 1, 2]), c2([5, 9]))
