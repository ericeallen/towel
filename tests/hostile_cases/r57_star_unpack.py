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

def s1(items):
    first, *rest = items
    rest = [r * 2 for r in rest]
    print("s1", first)
    return first, rest
def s2(items):
    first, *rest = items
    rest = [r * 2 for r in rest]
    print("s2", first)
    return first, rest
if __name__ == "__main__":
    print(s1([1, 2, 3]), s2([4]))
