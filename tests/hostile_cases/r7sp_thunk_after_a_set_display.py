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

# A set display hashes its element before the thunk: a custom __hash__ runs,
# and an unhashable element raises TypeError before the thunk's effect.
class Loud:
    def __init__(self, v): self.v = v
    def alpha(self):
        print("alpha called"); return self.v
    def beta(self):
        print("beta called"); return self.v * 2
class Key:
    def __hash__(self):
        print("hashing Key"); return 1
def f1(x, o):
    seen = {x}
    y = o.alpha()
    z = y + 1
    print("f1", y, z, len(seen))
    return z
def f2(x, o):
    seen = {x}
    y = o.beta() or 0
    z = y + 1
    print("f2", y, z, len(seen))
    return z
if __name__ == "__main__":
    print(f1(Key(), Loud(3)), f2(Key(), Loud(3)))
    for f in (f1, f2):
        try:
            f(["unhashable"], Loud(3))
        except TypeError as error:
            print("TypeError", error)
