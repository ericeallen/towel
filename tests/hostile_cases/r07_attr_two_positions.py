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

class O:
    def __init__(self): self.x = 3; self.z = 5
def a1(o):
    out = []
    if o.x:
        out.append(o.x)
    out.append("end")
    return out
def a2(o):
    out = []
    if o.x:
        out.append(o.z)
    out.append("end")
    return out
if __name__ == "__main__":
    print(a1(O()), a2(O()))
