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
def w1(n):
    out = []
    for _ in range(n):
        out.append(next(c))
    out.append("w")
    return out
def w2(n):
    out = []
    for _ in range(n):
        out.append(next(c) * 100)
    out.append("w")
    return out
if __name__ == "__main__":
    print(w1(2), w2(2))
