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

# A method of a Protocol class is a protocol member: a helper placed there is
# one more member every structural implementer lacks.
from typing import Protocol, runtime_checkable

@runtime_checkable
class P(Protocol):
    k: int
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
    def b(self, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
class Impl:
    k = 1
    def a(self, items):
        return 1
    def b(self, items):
        return 2
class Explicit(P):
    k = 2
if __name__ == "__main__":
    print(isinstance(Impl(), P), Explicit().a([1, 5, 9]), Explicit().b([1, 5, 9]))
    print(sorted(getattr(P, "__protocol_attrs__", ())))
