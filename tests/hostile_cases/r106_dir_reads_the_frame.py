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

def f(a, b):
    unused = 1
    total = a + b
    names = sorted(dir())
    count = len(names) + total
    return count, names
def g(a, b):
    total = a + b
    names = sorted(dir())
    count = len(names) + total
    return count * 2, names
if __name__ == "__main__":
    print(f(1, 2), g(1, 2))
