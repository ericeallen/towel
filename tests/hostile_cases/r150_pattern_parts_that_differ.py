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

# Nothing in a pattern can stand for a parameter. A literal or a dotted name
# where a pattern expects a value becomes a capture as a bare name, which
# matches anything (`case [__param_0, x]`), or a class pattern as a call
# (`case __param_0():`); a mapping key must be a literal or a dotted name.
import enum
class Color(enum.Enum):
    RED = 1
    BLUE = 2
class Point:
    __match_args__ = ("x",)
    def __init__(self, x):
        self.x = x
def red_first(value):
    print("color", 1)
    match value:
        case Color.RED:
            found = "hit"
        case _:
            found = "miss"
    return found
def blue_second(value):
    print("color", 2)
    match value:
        case Color.BLUE:
            found = "hit"
        case _:
            found = "miss"
    return found
def head_one(value):
    print("head", 1)
    match value:
        case [1, rest]:
            found = rest
        case _:
            found = "miss"
    return found
def head_two(value):
    print("head", 2)
    match value:
        case [2, rest]:
            found = rest
        case _:
            found = "miss"
    return found
def x_one(value):
    print("point", 1)
    match value:
        case Point(x=1):
            found = "hit"
        case _:
            found = "miss"
    return found
def x_two(value):
    print("point", 2)
    match value:
        case Point(x=2):
            found = "hit"
        case _:
            found = "miss"
    return found
def key_a(value):
    print("key", 1)
    match value:
        case {"a": found}:
            pass
        case _:
            found = "miss"
    return found
def key_b(value):
    print("key", 2)
    match value:
        case {"b": found}:
            pass
        case _:
            found = "miss"
    return found
if __name__ == "__main__":
    print(red_first(Color.RED), red_first(Color.BLUE), blue_second(Color.RED), blue_second(Color.BLUE))
    print(head_one([1, 5]), head_one([2, 6]), head_two([1, 5]), head_two([2, 6]))
    print(x_one(Point(1)), x_one(Point(2)), x_two(Point(1)), x_two(Point(2)))
    print(key_a({"a": 1}), key_a({"b": 2}), key_b({"a": 1}), key_b({"b": 2}))
