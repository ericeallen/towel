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

import functools
class C:
    def __init__(self, k): self.k = k
    @functools.lru_cache(maxsize=None)
    def m1(self, n):
        total = 0
        for i in range(n):
            total += i * self.k
        total += 1
        return total
    @functools.lru_cache(maxsize=None)
    def m2(self, n):
        total = 0
        for i in range(n):
            total += i * self.k
        total += 2
        return total
if __name__ == "__main__":
    c = C(3); print(c.m1(4), c.m2(4), c.m1(4), C.m1.cache_info().hits)
