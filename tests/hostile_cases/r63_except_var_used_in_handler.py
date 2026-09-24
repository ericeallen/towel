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

def x1(v):
    try:
        r = 1 / v
    except ZeroDivisionError as e:
        msg = str(e)
        r = None
    else:
        msg = "fine"
    print("x1", r)
    return msg
def x2(v):
    try:
        r = 2 / v
    except ZeroDivisionError as e:
        msg = str(e)
        r = None
    else:
        msg = "fine"
    print("x2", r)
    return msg
if __name__ == "__main__":
    print(x1(1), x1(0), x2(1), x2(0))
