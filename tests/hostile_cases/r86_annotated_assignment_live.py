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

from typing import Optional
class Style: pass
class Link: pass
def a1(rows):
    idx = 0
    # captured is a snapshot taken at the first row
    # and stays None until then
    captured: Optional[Style] = None
    for r in rows:
        if captured is None:
            captured = r
        idx += 1
    print("a1", idx, captured)
def a2(rows):
    first = True
    # state tracks the last seen row
    # as a string
    state: Optional[Link] = None
    for r in rows:
        if state is None:
            state = str(r)
        first = False
    print("a2", first, state)
if __name__ == "__main__":
    a1([5, 6])
    a2([7, 8])
