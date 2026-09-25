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

def t1(v):
    try:
        r = 10 / v
    except ZeroDivisionError:
        r = None
    else:
        note = "ok"
        r = r + 1
    print("t1", r)
    try:
        return note
    except UnboundLocalError:
        return "no-note"
def t2(v):
    try:
        r = 20 / v
    except ZeroDivisionError:
        r = None
    else:
        note = "ok"
        r = r + 1
    print("t2", r)
    try:
        return note
    except UnboundLocalError:
        return "no-note"
if __name__ == "__main__":
    print(t1(1), t1(0), t2(2), t2(0))
