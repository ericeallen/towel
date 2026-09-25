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

class T:
    def __init__(self): self.calls = []
    def strip(self, s): self.calls.append("strip"); return s.strip()
    def upper(self, s): self.calls.append("upper"); return s.upper()
def m1(t, s):
    parts = []
    parts.append(t.strip(s))
    parts.append(len(parts))
    parts.append(list(t.calls))
    return parts
def m2(t, s):
    parts = []
    parts.append(t.upper(s))
    parts.append(len(parts))
    parts.append(list(t.calls))
    return parts
if __name__ == "__main__":
    print(m1(T(), " a "), m2(T(), " b "))
