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

def u1(flag):
    if flag:
        val = 1
    other = 2
    print("u1")
    try:
        return val + other
    except UnboundLocalError:
        return "unbound"
def u2(flag):
    if flag:
        val = 10
    other = 2
    print("u2")
    try:
        return val + other
    except UnboundLocalError:
        return "unbound"
if __name__ == "__main__":
    print(u1(True), u1(False), u2(True), u2(False))
