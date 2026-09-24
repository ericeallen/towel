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

class P:
    def __init__(self): self.n = 0
    @property
    def val(self):
        self.n += 1; return self.n
def v1(p):
    print("start")
    acc = []
    acc.append(1)
    acc.append(p.val)
    acc.append(p.n)
    return acc
def v2(p):
    print("start")
    acc = []
    acc.append(1)
    acc.append(p.n)
    acc.append(p.n)
    return acc
if __name__ == "__main__":
    print(v1(P()), v2(P()))
