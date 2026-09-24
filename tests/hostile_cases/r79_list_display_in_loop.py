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

def l1(rows):
    acc = []
    for r in rows:
        acc.append([])
        acc[-1].append(r)
    acc.append("l1")
    return acc
def l2(rows):
    acc = []
    for r in rows:
        acc.append(())
        acc[-1:] = [(r,)]
    acc.append("l2")
    return acc
def l3(rows):
    acc = []
    for r in rows:
        acc.append([])
        acc[-1].append(r * 2)
    acc.append("l3")
    return acc
if __name__ == "__main__":
    print(l1([1, 2]), l2([3]), l3([4, 5]))
