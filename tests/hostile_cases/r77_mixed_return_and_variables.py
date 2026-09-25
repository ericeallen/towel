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

class D:
    def __init__(self, func): self.func = func
def g1(obj, self):
    if obj is None:
        return self
    cls = obj.__class__
    name = cls.__name__
    return name.upper() + "1"
def g2(obj, self):
    if obj is None:
        return self
    cls = obj.__class__
    name = cls.__name__
    print(name)
    return name
if __name__ == "__main__":
    print(g1(None, "s"), g1(D(1), "s"), g2(None, "t"), g2([], "t"))
