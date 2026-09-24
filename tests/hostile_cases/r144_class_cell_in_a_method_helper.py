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

# ``__class__`` is the defining class's cell, not a module name: a method helper
# hosted in the base class must take it as a parameter, not read it bare.
class Base:
    def describe(self):
        return "base"
    def build(self, n):
        return ["b"] * n
class C1(Base):
    def build(self, n):
        parts = super().build(n)
        parts.append(self.describe())
        parts.append(__class__.__name__)
        parts.append(len(parts))
        return parts
class C2(Base):
    def build(self, n):
        parts = super().build(n)
        parts.append(self.describe())
        parts.append(__class__.__name__)
        parts.append(len(parts))
        return parts
class C3(Base):
    def build(self, n):
        parts = super(C3, self).build(n)
        parts.append(self.describe())
        parts.append(type(self).__name__)
        parts.append(len(parts))
        return parts
    def describe(self):
        return "c3"
if __name__ == "__main__":
    print(C1().build(1), C2().build(2), C3().build(1))
