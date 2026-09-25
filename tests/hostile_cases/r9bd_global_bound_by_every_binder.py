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

# Each function declares its name global, and the shared block after its own
# first line binds the name by a construct other than a plain assignment: an
# import, a tuple target, a match capture, a walrus. The helper must declare
# each global too, or its binding stays in the helper and the module's name
# is never written; only plain assignments were seen. A third site, whose
# function declares nothing, must not share the helper that declares it
# (triple_3).
import contextlib

imported = "module imported"
pair = "module pair"
captured = "module captured"
walrused = "module walrused"
tripled = "module tripled"


def import_1(a):
    global imported
    print("first site", a)
    import contextlib as imported
    print("import", imported.__name__, a)
    print("done", a * 2)
    return a


def import_2(b):
    global imported
    assert b is not None, "second site"
    import contextlib as imported
    print("import", imported.__name__, b)
    print("done", b * 2)
    return b


def tuple_1(a):
    global pair
    print("first site", a)
    first, pair = a, [a]
    print("tuple", first, a)
    print("done", a * 3)
    return a


def tuple_2(b):
    global pair
    assert b is not None, "second site"
    first, pair = b, [b]
    print("tuple", first, b)
    print("done", b * 3)
    return b


def match_1(a):
    global captured
    print("first site", a)
    match [a, a]:
        case [captured, _]:
            print("match", a)
    print("done", a * 4)
    return a


def match_2(b):
    global captured
    assert b is not None, "second site"
    match [b, b]:
        case [captured, _]:
            print("match", b)
    print("done", b * 4)
    return b


def walrus_1(a):
    global walrused
    print("first site", a)
    print("walrus", (walrused := a + 1), a)
    print("done", a * 5)
    return a


def walrus_2(b):
    global walrused
    assert b is not None, "second site"
    print("walrus", (walrused := b + 1), b)
    print("done", b * 5)
    return b


def triple_1(a):
    global tripled
    print("first site", a)
    tripled = a * 6
    print("triple", a)
    print("done", a * 6)
    return a


def triple_2(b):
    global tripled
    assert b is not None, "second site"
    tripled = b * 6
    print("triple", b)
    print("done", b * 6)
    return b


def triple_3(c):
    tripled = c * 6
    print("triple", c)
    print("done", c * 6)
    return c


if __name__ == "__main__":
    for function, value in (
        (import_1, 1), (import_2, 2), (tuple_1, 3), (tuple_2, 4), (match_1, 5),
        (match_2, 6), (walrus_1, 7), (walrus_2, 8), (triple_1, 9), (triple_2, 10),
        (triple_3, 11),
    ):
        print(function.__name__, function(value))
        print("module:", repr(imported)[:30], pair, captured, walrused, tripled)
