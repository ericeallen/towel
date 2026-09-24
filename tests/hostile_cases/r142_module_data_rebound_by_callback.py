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

# ``limit`` is module data a callback rebinds between the block's two reads.
# A helper that read it once, at the call, would use the old value for the
# second read; a bare module reference inside the helper reads it when the
# block did.
limit = 1
def bump():
    global limit
    limit += 10
    return 0
def first(items):
    items = list(items)
    total = len(items) + limit
    total += bump()
    total += limit
    return total
def second(items):
    total = len(items) + limit
    total += bump()
    total += limit
    return total * 2
if __name__ == "__main__":
    print(first([1]), second([1, 2]), limit)
