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

# The block's del is its function's only binding of X, and the function reads
# X after the block. The del makes X local to inner, so the read raises
# UnboundLocalError; moved into a helper, the del would leave inner reading
# the enclosing function's X. Both functions are named inner, which once hid
# the read from the check for names the block leaves unbound.
def first(n):
    X = "enclosing X"

    def inner():
        try:
            del X
        except UnboundLocalError:
            print("nothing to delete", n)
        print("start", n)
        try:
            after = X
        except (UnboundLocalError, NameError) as error:
            after = type(error).__name__
        return after

    return inner()


def second(q):
    X = "enclosing X"

    def inner():
        try:
            del X
        except UnboundLocalError:
            print("nothing to delete", q)
        print("start", q)
        try:
            after = X
        except (UnboundLocalError, NameError) as error:
            after = type(error).__name__
        return after

    return inner()


if __name__ == "__main__":
    for function in (first, second):
        try:
            print(function.__name__, "->", repr(function(2)))
        except Exception as error:
            print(function.__name__, "raised", type(error).__name__, error)
