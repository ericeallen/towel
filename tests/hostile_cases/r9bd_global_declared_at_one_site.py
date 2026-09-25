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

# The two blocks are the same, but only f1 declares total global, so its
# block writes the module's total while f2's binds a local. One helper
# declared global for both would make f2 write the module's total; declared
# for neither, f1 would stop writing it. The helper's declarations came from
# the first site's function alone.
total = "module total"


def f1(a, b):
    global total
    total = a * 2
    print("block", a, b[:2])
    print("twice", a * 7)
    return ("done", a)


def f2(x, y):
    total = x * 2
    print("block", x, y[:2])
    print("twice", x * 7)
    return ("done", x)


if __name__ == "__main__":
    print(f1(1, [2]), total)
    print(f2(5, [2]), total)
