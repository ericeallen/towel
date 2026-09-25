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

# Round-4 P1-02: a ``for`` or ``with`` target that stores into an attribute
# reads the object it stores into, so the helper must read its parameter
# there too; it kept the template's ``box`` and raised NameError.
import contextlib


class Box:
    v = 0


def load_all(box, items, tag):
    for box.v in items:
        print("loading", box.v, tag)
    print("loaded", box.v)
    return box.v


def load_each(target, values, label):
    for target.v in values:
        print("loading", target.v, label)
    print("loaded", target.v)
    return target.v


def enter_one(box, ctx, tag):
    with ctx as box.v:
        print("inside", box.v, tag)
    print("left", box.v)
    return box.v


def enter_two(target, manager, label):
    with manager as target.v:
        print("inside", target.v, label)
    print("left", target.v)
    return target.v


def fill_slots(slots, items, tag):
    for slots[0] in items:
        print("slot", slots, tag)
    print("filled", slots)
    return slots


def fill_cells(cells, values, label):
    for cells[0] in values:
        print("slot", cells, label)
    print("filled", cells)
    return cells


if __name__ == "__main__":
    print(load_all(Box(), [1, 2], "t"), load_each(Box(), [3], "u"))
    print(enter_one(Box(), contextlib.nullcontext(5), "t"))
    print(enter_two(Box(), contextlib.nullcontext(6), "u"))
    print(fill_slots([0, 9], [1, 2], "t"), fill_cells([0], [3], "u"))
