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

# A type statement binds its name in the function, like an assignment: the
# block's "type X = int" is the function's only binding of X, and the read
# before the block raises UnboundLocalError. Python 3.12 and later.
X = "module X"


def f1(n):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    print("start", n)
    type X = list[int]
    print("aliased", n)
    return before


def f2(q):
    try:
        before = X
    except UnboundLocalError as error:
        before = type(error).__name__
    print("start", q)
    type X = list[int]
    print("aliased", q)
    return before


if __name__ == "__main__":
    for function in (f1, f2):
        try:
            print(function.__name__, "->", repr(function(2)))
        except Exception as error:
            print(function.__name__, "raised", type(error).__name__, error)
