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

def o1(items):
    count = 0
    def inc():
        nonlocal count
        count += 1
    for i in items:
        inc()
        inc()
    print("o1", count)
    return count
def o2(items):
    count = 0
    def inc():
        nonlocal count
        count += 1
    for i in items:
        inc()
        inc()
    print("o2", count)
    return count
if __name__ == "__main__":
    print(o1([1, 2]), o2([3]))
