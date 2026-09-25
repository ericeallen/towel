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
    out = []
    out.append("start")
    if (m := re.match(r"\d+", s)):
        out.append(m.group(0))
    out.append("end")
    return out, m
def w2(s):
    out = []
    out.append("start")
    if s.isdigit():
        out.append(s)
    out.append("end")
    return out, s
if __name__ == "__main__":
    print(w1("12a"), w1("x"), w2("3"), w2("y"))
