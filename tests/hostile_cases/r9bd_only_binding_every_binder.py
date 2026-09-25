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

# Every construct that binds a name, each holding its function's only binding
# of it inside the shared block, and each function reading the name before
# the block, where the original raises UnboundLocalError. Once the block moves
# the name is no longer local: the read finds the module's X, the builtin len,
# or the enclosing function's cell, unless the pair is declined.
import contextlib

X = "module X"


def assign_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    X = n + 1
    print("assign", X, n)
    print("done", n * 2)
    return before


def assign_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    X = q + 1
    print("assign", X, q)
    print("done", q * 2)
    return before


def for_1(n):
    try:
        before = len
    except UnboundLocalError as error:
        before = type(error).__name__
    for len in range(n):
        print("for", len, n)
    print("done", n * 3)
    return before


def for_2(q):
    try:
        before = len
    except UnboundLocalError as error:
        before = type(error).__name__
    for len in range(q):
        print("for", len, q)
    print("done", q * 3)
    return before


def with_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    with contextlib.nullcontext(n) as X:
        print("with", X, n)
    print("done", n * 4)
    return before


def with_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    with contextlib.nullcontext(q) as X:
        print("with", X, q)
    print("done", q * 4)
    return before


def except_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    try:
        raise ValueError(n)
    except ValueError as X:
        print("except", X, n)
    print("done", n * 5)
    return before


def except_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    try:
        raise ValueError(q)
    except ValueError as X:
        print("except", X, q)
    print("done", q * 5)
    return before


def import_1(n):
    try:
        before = contextlib
    except UnboundLocalError as error:
        before = type(error).__name__
    import contextlib
    print("import", contextlib.__name__, n)
    print("done", n * 6)
    return before


def import_2(q):
    try:
        before = contextlib
    except UnboundLocalError as error:
        before = type(error).__name__
    import contextlib
    print("import", contextlib.__name__, q)
    print("done", q * 6)
    return before


def walrus_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    print("walrus", (X := n * 7), n)
    print("again", X)
    print("done", n * 7)
    return before


def walrus_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    print("walrus", (X := q * 7), q)
    print("again", X)
    print("done", q * 7)
    return before


def match_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    match [n, n]:
        case [X, _]:
            print("match", X, n)
    print("done", n * 8)
    return before


def match_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    match [q, q]:
        case [X, _]:
            print("match", X, q)
    print("done", q * 8)
    return before


def augmented_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    try:
        X += n
    except UnboundLocalError:
        print("augmented", n)
    print("done", n * 9)
    return before


def augmented_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    try:
        X += q
    except UnboundLocalError:
        print("augmented", q)
    print("done", q * 9)
    return before


def deleted_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    try:
        del X
    except UnboundLocalError:
        print("deleted", n)
    print("done", n * 10)
    return before


def deleted_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    try:
        del X
    except UnboundLocalError:
        print("deleted", q)
    print("done", q * 10)
    return before


def annotated_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    X: int
    print("annotated", n)
    print("done", n * 11)
    return before


def annotated_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    X: int
    print("annotated", q)
    print("done", q * 11)
    return before


def defined_1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__

    def X():
        return n

    print("defined", X(), n)
    print("done", n * 12)
    return before


def defined_2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__

    def X():
        return q

    print("defined", X(), q)
    print("done", q * 12)
    return before


def closure_1(n):
    X = "closure X"

    def inner():
        try:
            before = X
        except UnboundLocalError as error:
            before = type(error).__name__
        X = n * 13
        print("closure", X, n)
        print("done", n * 13)
        return before

    return inner()


def closure_2(q):
    X = "closure X"

    def inner():
        try:
            before = X
        except UnboundLocalError as error:
            before = type(error).__name__
        X = q * 13
        print("closure", X, q)
        print("done", q * 13)
        return before

    return inner()


if __name__ == "__main__":
    for function in (
        assign_1, assign_2, for_1, for_2, with_1, with_2, except_1, except_2,
        import_1, import_2, walrus_1, walrus_2, match_1, match_2, augmented_1,
        augmented_2, deleted_1, deleted_2, annotated_1, annotated_2, defined_1,
        defined_2, closure_1, closure_2,
    ):
        try:
            print(function.__name__, "->", repr(function(2)))
        except Exception as error:
            print(function.__name__, "raised", type(error).__name__, error)
