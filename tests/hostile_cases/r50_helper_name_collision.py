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

def _extracted_func_0(x):
    return "user-defined"
def __extracted_func_1(x):
    return "user-defined-2"
def u1(items):
    out = []
    for i in items:
        out.append(i * 3)
    out.append(_extracted_func_0(1))
    return out
def u2(items):
    out = []
    for i in items:
        out.append(i * 3)
    out.append(__extracted_func_1(1))
    return out
if __name__ == "__main__":
    print(u1([1]), u2([2]))
