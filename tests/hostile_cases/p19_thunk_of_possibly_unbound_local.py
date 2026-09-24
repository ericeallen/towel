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

# A local bound on one path only, read by the shared block. Passed as a thunk,
# the unfilled closure cell raises NameError where the original raised
# UnboundLocalError, so the handler below stops matching.
def f(v, flag):
    if flag:
        x = v
    a = v + 1
    b = a * 2
    c = b + x
    return c


def g(v, flag):
    if flag:
        x = v
    a = v + 2
    b = a * 2
    c = b + x
    return c


if __name__ == "__main__":
    for fn in (f, g):
        for fl in (True, False):
            try:
                print(fn(1, fl))
            except UnboundLocalError as error:
                print("UnboundLocalError", error)
            except NameError as error:
                print("NameError", error)
