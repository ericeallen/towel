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

def g(*xs): return sum(xs)
def s1(a, b):
    out = []
    out.append(g(*a))
    out.append(len(a))
    out.append("s")
    return out
def s2(a, b):
    out = []
    out.append(g(*(a + b)))
    out.append(len(a))
    out.append("s")
    return out
if __name__ == "__main__":
    print(s1([1, 2], [3]), s2([1, 2], [3]))
