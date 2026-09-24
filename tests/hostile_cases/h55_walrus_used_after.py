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

import re
def w1(s):
    parts = []
    parts.append("x")
    if (m := re.match(r"(\d+)", s)):
        parts.append(m.group(1))
    parts.append("y")
    return parts, m
def w2(s):
    parts = []
    parts.append("x")
    if (m := re.match(r"(\d+)", s)):
        parts.append(m.group(1) * 2)
    parts.append("y")
    return parts, m
if __name__ == "__main__":
    print(w1("12a"), w1("zz"), w2("3"))
