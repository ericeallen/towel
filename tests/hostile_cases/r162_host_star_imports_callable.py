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

# ``Callable`` is collections.abc's, bound by a star import no list of the
# module's imports names. The copied annotation ``Callable[[], int]`` needs
# no import at all; ``from typing import Callable`` after the star import
# would make ``case Callable():`` raise "called match pattern must be a class".
from collections.abc import *


def first(make: Callable[[], int], label: str) -> int:
    value = make()
    text = f"{label}:{value}"
    print(text)
    return value + 1


def second(make: Callable[[], int], label: str) -> int:
    value = make()
    text = f"{label}:{value}"
    print(text)
    return value + 1


def kind(value: object) -> str:
    match value:
        case Callable():
            return "callable"
        case _:
            return "other"


if __name__ == "__main__":
    print(first(lambda: 1, "a"), second(lambda: 2, "b"))
    print(kind(len), kind(3))
