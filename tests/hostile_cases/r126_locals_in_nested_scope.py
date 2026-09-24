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

def lo1(a):
    b = a + 1
    def inner():
        c = b * 2
        d = c + 1
        return sorted(locals()), c, d
    r = inner()
    return r, b
def lo2(a):
    b = a + 1
    def inner():
        c = b * 2
        d = c + 1
        return sorted(locals()), c, d
    r = inner()
    return r, b, 2
def lo3(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(vars())
    return names, d
def lo4(a):
    b = a + 1
    c = b * 2
    d = c + 1
    names = sorted(vars())
    return names, d, 4
if __name__ == "__main__":
    print(lo1(1), lo2(2), lo3(1), lo4(2))
