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

def p1(items):
    acc = []
    for it in items:
        acc.append(it + 1)
    print("p1", acc)
def p2(items):
    res = []
    for x in items:
        res.append(x + 1)
    print("p2", res)
def p3(items):
    vals = []
    for y in items:
        vals.append(y + 1)
    print("p3", vals)
if __name__ == "__main__":
    p1([1]); p2([2]); p3([3])
