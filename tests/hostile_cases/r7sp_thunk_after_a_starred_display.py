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

# ``[*x]`` iterates x, which runs __iter__, before the thunk.
class Loud:
    def __init__(self, v): self.v = v
    def alpha(self):
        print("alpha called"); return self.v
    def beta(self):
        print("beta called"); return self.v * 2
    def __iter__(self):
        print("iterating"); return iter([self.v])
def f1(x, o):
    items = [*x]
    y = o.alpha()
    z = y + 1
    print("f1", y, z, items)
    return z
def f2(x, o):
    items = [*x]
    y = o.beta() or 0
    z = y + 1
    print("f2", y, z, items)
    return z
if __name__ == "__main__":
    print(f1(Loud(7), Loud(3)), f2(Loud(7), Loud(3)))
