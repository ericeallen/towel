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

# A class pattern calls nothing but evaluates its class name, and a mapping
# key evaluates its dotted name, as the match runs: in a helper those names
# must arrive as arguments, wherever in the pattern they stand.
def by_class_first(value, kind):
    print("class", 1)
    match value:
        case kind():
            found = "is-kind"
        case _:
            found = "other"
    return found
def by_class_second(value, kind):
    print("class", 2)
    match value:
        case kind():
            found = "is-kind"
        case _:
            found = "other"
    return found
def in_sequence_first(value, kind):
    print("sequence", 1)
    match value:
        case [kind()]:
            found = "one-kind"
        case _:
            found = "other"
    return found
def in_sequence_second(value, kind):
    print("sequence", 2)
    match value:
        case [kind()]:
            found = "one-kind"
        case _:
            found = "other"
    return found
def in_alternative_first(value, kind):
    print("alternative", 1)
    match value:
        case str() | kind():
            found = "str-or-kind"
        case _:
            found = "other"
    return found
def in_alternative_second(value, kind):
    print("alternative", 2)
    match value:
        case str() | kind():
            found = "str-or-kind"
        case _:
            found = "other"
    return found
def by_key_first(value, key):
    print("key", 1)
    match value:
        case {key.real: found}:
            pass
        case _:
            found = None
    return found
def by_key_second(value, key):
    print("key", 2)
    match value:
        case {key.real: found}:
            pass
        case _:
            found = None
    return found
if __name__ == "__main__":
    print(by_class_first(3, int), by_class_second("s", int))
    print(in_sequence_first([3], int), in_sequence_second(["s"], int))
    print(in_alternative_first(3, int), in_alternative_second(3.5, int))
    print(by_key_first({2: "two"}, 2), by_key_second({3: "three"}, 2))
