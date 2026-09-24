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

class U:
    def __init__(self): self.name = "n"; self.mail = "m"
    @property
    def loud(self):
        print("loud-accessed"); return self.name.upper()
def f1(u):
    print("start")
    parts = []
    parts.append(f"<{u.loud}>")
    parts.append(f"<{u.loud}>")
    return parts
def f2(u):
    print("start")
    parts = []
    parts.append(f"<{u.mail}>")
    parts.append(f"<{u.mail}>")
    return parts
if __name__ == "__main__":
    print(f1(U()), f2(U()))
