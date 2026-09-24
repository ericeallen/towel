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

# The helper's annotations name Late, so it may be placed after Late to spell
# them bare; but the assignment before Late already calls f, which needs it.
from __future__ import annotations
from typing import Optional


def f(x: Optional[Late], items: list[int]) -> int:
    total = 0
    for item in items:
        if item > 4:
            total += item * 2
        else:
            total -= item
    result = total + (0 if x is None else x.k)
    return result * 3


def g(x: Optional[Late], items: list[int]) -> int:
    total = 0
    for item in items:
        if item > 5:
            total += item * 2
        else:
            total -= item
    result = total + (0 if x is None else x.k)
    return result * 3


Y = f(None, [1, 5, 9])


class Late:
    k = 1


if __name__ == "__main__":
    print(Y, g(Late(), [1, 5, 9]))
