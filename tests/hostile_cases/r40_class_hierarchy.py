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

class Base:
    def __init__(self, x, y): self.x = x; self.y = y
    @property
    def noisy(self):
        print("noisy"); return self.x
class A(Base):
    def run(self):
        if not self.noisy:
            return "empty"
        if len(self.noisy) < 2:
            return "short"
        return self.noisy.upper()
class B(Base):
    def run(self):
        if not self.y:
            return "empty"
        if len(self.y) < 2:
            return "short"
        return self.y.upper()
if __name__ == "__main__":
    print(A("ab", "cd").run(), B("", "e").run(), A("", "zz").run())
