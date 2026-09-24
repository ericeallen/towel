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

trace = []
def compute_a():
    trace.append("compute_a"); return 1
def compute_b():
    trace.append("compute_b"); return 2
def f1(items):
    trace.append("start1")
    total = 0
    for it in items:
        total += it
    x = compute_a()
    trace.append("mid1")
    return total + x
def f2(items):
    trace.append("start2")
    total = 0
    for it in items:
        total += it
    x = compute_b()
    trace.append("mid2")
    return total + x
if __name__ == "__main__":
    print(f1([1, 2]), f2([3])); print(trace)
