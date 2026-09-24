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

calls = []
def side(tag): calls.append(tag); return True
def s1(flag):
    calls.append("s1")
    ok = flag and side("a")
    calls.append("after")
    return ok
def s2(flag):
    calls.append("s2")
    ok = flag and side("b")
    calls.append("after")
    return ok
if __name__ == "__main__":
    print(s1(False), s1(True), s2(False), s2(True), calls)
