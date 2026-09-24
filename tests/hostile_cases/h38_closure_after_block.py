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

def a1(items):
    total = 0
    for i in items:
        total += i
    limit = total * 2
    def check(v):
        return v < limit
    return [check(i) for i in items]
def a2(items):
    total = 0
    for i in items:
        total += i
    limit = total * 3
    def check(v):
        return v < limit
    return [check(i) for i in items]
if __name__ == "__main__":
    print(a1([1, 2, 3]), a2([1, 5]))
