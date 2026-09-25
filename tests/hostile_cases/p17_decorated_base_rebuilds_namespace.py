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

# The common ancestor is decorated, and the decorator keeps only public names.
def public_only(cls):
    ns = {k: v for k, v in vars(cls).items() if not k.startswith("_") or k.startswith("__")}
    ns.pop("__dict__", None)
    ns.pop("__weakref__", None)
    return type(cls.__name__, cls.__bases__, ns)

@public_only
class Base:
    k = 0
class A(Base):
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
class B(Base):
    def b(self, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3 + self.k
if __name__ == "__main__":
    print(A().a([1, 5, 9]), B().b([1, 5, 9]))
