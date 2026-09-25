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

# A loop variable is unbound after a loop that never ran.
def f(items):
    for it in items:
        last = it
    a = len(items) + 1
    b = a * 2
    c = b + last
    return c


def g(items):
    for it in items:
        last = it
    a = len(items) + 2
    b = a * 2
    c = b + last
    return c


if __name__ == "__main__":
    for fn in (f, g):
        for s in ([1, 2], []):
            try:
                print(fn(s))
            except UnboundLocalError:
                print("UnboundLocalError")
            except NameError:
                print("NameError")
