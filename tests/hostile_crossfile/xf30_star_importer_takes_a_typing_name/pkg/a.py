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

# The host has no __all__, so every public name it binds reaches b.py's star
# import. Its helper's annotations need Any (and, where a checker infers the
# thunk, Callable); an import of either from typing would replace b.py's own.
class Config:
    def __init__(self) -> None:
        self.scale = 2
        self.factor = 3


def f1(xs: list[int], cfg: Config) -> int:
    total = 0
    for x in xs:
        total += x * cfg.scale
        print("item", x)
    print("f1", total)
    return total


def f2(ys: list[int], cfg: Config) -> int:
    total = 0
    for y in ys:
        total += y * cfg.factor
        print("item", y)
    print("f2", total)
    return total
