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

# The block holds its function's only binding of X, and code of the function
# outside the block reads X through its scope: a comprehension and a class
# body, which run where they stand, and a nested function declaring X
# nonlocal. Each read raises in the original or sees the block's binding; with
# the block moved, X would stop being local and each would find the module's
# X, or the nonlocal would no longer compile.
X = "module X"


def comprehension_1(n):
    try:
        before = [X for _ in range(1)]
    except (UnboundLocalError, NameError) as error:
        before = type(error).__name__
    X = n * 2
    print("comprehension", X, n)
    print("done", n + 1)
    return before


def comprehension_2(q):
    try:
        before = [X for _ in range(1)]
    except (UnboundLocalError, NameError) as error:
        before = type(error).__name__
    X = q * 2
    print("comprehension", X, q)
    print("done", q + 1)
    return before


def class_body_1(n):
    try:

        class Holder:
            value = X

        before = Holder.value
    except (UnboundLocalError, NameError) as error:
        before = type(error).__name__
    X = n * 3
    print("class body", X, n)
    print("done", n + 2)
    return before


def class_body_2(q):
    try:

        class Holder:
            value = X

        before = Holder.value
    except (UnboundLocalError, NameError) as error:
        before = type(error).__name__
    X = q * 3
    print("class body", X, q)
    print("done", q + 2)
    return before


def outer(n):
    X = "outer X"

    def setter_1(v):
        def set_it():
            nonlocal X
            X = v

        X = v * 4
        print("setter", X, v)
        print("done", v + 3)
        set_it()
        return X

    def setter_2(w):
        def set_it():
            nonlocal X
            X = -w

        X = w * 4
        print("setter", X, w)
        print("done", w + 3)
        set_it()
        return X

    return setter_1(n), setter_2(n), X


if __name__ == "__main__":
    for function in (comprehension_1, comprehension_2, class_body_1, class_body_2, outer):
        try:
            print(function.__name__, "->", repr(function(2)))
        except Exception as error:
            print(function.__name__, "raised", type(error).__name__, error)
