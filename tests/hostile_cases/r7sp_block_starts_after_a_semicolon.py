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

# Each block starts at the second statement of a line: the call must keep
# the first one, which binds the name the call passes.
def f1(n):
    a = n + 1; b = a * 2
    c = a + b
    print("f1", a, b, c)
    return c
def f2(n):
    a = n + 2; b = a * 2
    c = a + b
    print("f2", a, b, c)
    return c
def f3(n):
    a = n + 3; b = a * 2
    c = a + b
    print("f3", a, b, c)
    return c
if __name__ == "__main__":
    print(f1(1), f2(1), f3(1))
