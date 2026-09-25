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

def outer_a(items):
    out = []
    for i in items:
        if i > 0:
            out.append(i)
            out.append(i * 2)
    out.append("a")
    return out
def outer_b(items):
    out = []
    for i in items:
        if i > 0:
            out.append(i)
            out.append(i * 2)
    out.append("b")
    return out
def inner_c(i, out):
    if i > 0:
        out.append(i)
        out.append(i * 2)
    return out
if __name__ == "__main__":
    print(outer_a([1, -1, 2]), outer_b([3]), inner_c(4, []), inner_c(-4, ["z"]))
