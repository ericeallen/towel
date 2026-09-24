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

def p1(x):
    if x < 0:
        return "neg"
    y = x * 2
    z = y + 1
    if z > 100:
        return "big"
    print("after1", y, z)
    return y - z
def p2(x):
    if x < 0:
        return "neg"
    y = x * 3
    z = y + 1
    if z > 100:
        return "big"
    print("after2", y, z)
    return y - z
if __name__ == "__main__":
    print(p1(-1), p1(5), p1(60), p2(-2), p2(4), p2(50))
