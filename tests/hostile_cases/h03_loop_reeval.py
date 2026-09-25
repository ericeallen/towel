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

import itertools
c = itertools.count()
def nxt(): return next(c)
def r1(n):
    acc = []
    for i in range(n):
        acc.append(i)
        acc.append(nxt())
    acc.append("end")
    return acc
def r2(n):
    acc = []
    for i in range(n):
        acc.append(i)
        acc.append(nxt() * 10)
    acc.append("end")
    return acc
if __name__ == "__main__":
    print(r1(3), r2(2))
