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

# Decorators known to keep the class and its namespace: the helper stays a
# method, and every one of these classes behaves as before.
import dataclasses
import enum
import functools
from dataclasses import dataclass
from typing import final

@dataclass(frozen=True, slots=True)
class Point:
    x: int
    k: int = 0
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
@functools.total_ordering
@final
@dataclasses.dataclass
class Rank:
    k: int
    def __lt__(self, other):
        return self.k < other.k
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
@enum.unique
class Colour(enum.Enum):
    RED = 1
    GREEN = 2
    @property
    def k(self):
        return self.value
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
if __name__ == "__main__":
    p, r = Point(1), Rank(3)
    print(p.a([1, 5, 9]), p.b([1, 5, 9]), r.a([1, 5, 9]), r.b([1, 5, 9]), r <= Rank(4))
    print(Colour.RED.a([1, 5, 9]), Colour.GREEN.b([1, 5, 9]), list(Colour))
    print(sorted(Point.__slots__), dataclasses.fields(Point)[1].name)
