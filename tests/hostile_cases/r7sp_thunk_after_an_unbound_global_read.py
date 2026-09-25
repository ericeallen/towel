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

# Reading a global nothing binds raises NameError before the thunk's effect.
class Loud:
    def __init__(self, v): self.v = v
    def alpha(self):
        print("alpha called"); return self.v
    def beta(self):
        print("beta called"); return self.v * 2
def f1(x, o):
    base = NOT_DEFINED_YET
    y = o.alpha()
    z = y + base
    print("f1", y, z)
    return z
def f2(x, o):
    base = NOT_DEFINED_YET
    y = o.beta() or 0
    z = y + base
    print("f2", y, z)
    return z
if __name__ == "__main__":
    for f in (f1, f2):
        try:
            print(f(0, Loud(3)))
        except NameError as error:
            print("NameError", error)
    NOT_DEFINED_YET = 100
    print(f1(0, Loud(3)), f2(0, Loud(3)))
