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

def f(xs):
    fs = []
    for x in xs:
        fs.append(lambda: x)
    k = len(fs) + 1
    print("f")
    x = 99
    return [h() for h in fs], k
def g(xs):
    fs = []
    for x in xs:
        fs.append(lambda: x)
    k = len(fs) + 1
    x = 42
    print("g")
    return [h() for h in fs], k * 2
if __name__ == "__main__":
    print(f([1, 2, 3]), g([4, 5]))
