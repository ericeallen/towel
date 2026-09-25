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

from typing import Callable


class Loud:
    def __init__(self, v: int) -> None:
        self.v = v

    def alpha(self) -> int:
        print("alpha")
        return self.v

    def beta(self) -> int:
        print("beta")
        return self.v * 2


def tr(tag: str, v: int) -> int:
    print("tr", tag)
    return v


def f1(o: Loud) -> int:
    g: Callable[[], int] = lambda k=tr("default", 1): k
    y = o.alpha()
    z = g() + y
    print("f1", y, z)
    return z


def f2(o: Loud) -> int:
    g: Callable[[], int] = lambda k=tr("default", 1): k
    y = o.beta() or 0
    z = g() + y
    print("f2", y, z)
    return z
