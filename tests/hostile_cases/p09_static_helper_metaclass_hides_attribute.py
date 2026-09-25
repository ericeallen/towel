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

# A metaclass that intercepts class attribute lookup sees a helper reached
# through the class, where the original methods made no such lookup.
class Meta(type):
    def __getattribute__(cls, name):
        if name.startswith("_extracted"):
            raise AttributeError(name)
        return super().__getattribute__(name)
class C(metaclass=Meta):
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
    def b(self, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
if __name__ == "__main__":
    print(C().a([1, 5, 9]), C().b([1, 5, 9]))
