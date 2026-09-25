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

class Box:
    def __init__(self): self.a = None; self.b = None
def i1(box, v):
    checked = v if v is not None else 0
    box.a = checked
    box.a = box.a + 1
    return box
def i2(box, v):
    checked = v if v is not None else 0
    box.b = checked
    box.b = box.b + 1
    return box
if __name__ == "__main__":
    print(vars(i1(Box(), 1)), vars(i2(Box(), None)))
