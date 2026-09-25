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

def d1(v):
    x = v + 1
    print("have", x)
    del x
    print("deleted")
    try:
        return x
    except UnboundLocalError:
        return "unbound1"
def d2(v):
    x = v + 2
    print("have", x)
    del x
    print("deleted")
    try:
        return x
    except UnboundLocalError:
        return "unbound2"
if __name__ == "__main__":
    print(d1(1), d2(2))
