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

# The mirror of a block holding a function's only binding: here the binding
# stays outside the block and the block holds the read. "id += 1" makes id
# local to the function, so the block's read of id raises UnboundLocalError.
# The analysis dropped id as a builtin, since no scope it recorded binds it,
# so the helper read id bare and found the builtin.
def f1(n):
    try:
        id += 1
    except UnboundLocalError:
        print("no id yet", n)
    print("start", n)
    try:
        seen = id
    except (UnboundLocalError, NameError) as error:
        seen = type(error).__name__
    print("seen", seen)
    return seen


def f2(q):
    try:
        id += 1
    except UnboundLocalError:
        print("no id yet", q * 2)
    print("start", q)
    try:
        seen = id
    except (UnboundLocalError, NameError) as error:
        seen = type(error).__name__
    print("seen", seen)
    return seen


if __name__ == "__main__":
    for function in (f1, f2):
        try:
            print(function.__name__, "->", repr(function(2)))
        except Exception as error:
            print(function.__name__, "raised", type(error).__name__, error)
