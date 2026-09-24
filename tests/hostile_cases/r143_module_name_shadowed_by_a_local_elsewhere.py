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

# ``scale`` is a module name at two sites and a local at a third that has the
# same block shape. The helper reads the module's ``scale`` bare, so the third
# site must not join it: its own ``scale`` is a different binding.
scale = 10
def a(items):
    items = list(items)
    total = sum(items)
    total *= scale
    total += 1
    return total
def b(items):
    total = sum(items)
    total *= scale
    total += 1
    return total - 1
def c(items, scale):
    total = sum(items)
    total *= scale
    total += 1
    return total + 100
if __name__ == "__main__":
    print(a([1, 2]), b([3]), c([4], 2))
