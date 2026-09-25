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

class Chars:
    def __init__(self, letters, digits): self.letters = letters; self.digits = digits
    @property
    def noisy(self):
        print("noisy"); return self.letters
def ident(c):
    chars = set(c.noisy) | set("xy_")
    ordered = sorted(chars)
    return "".join(ordered)
def body(c):
    chars = set(c.digits) | set("0")
    ordered = sorted(chars)
    return "".join(ordered)
def both(c):
    chars = set(c.noisy) | set("q")
    ordered = sorted(chars)
    return "".join(ordered)
if __name__ == "__main__":
    c = Chars("ba", "21"); print(ident(c), body(c), both(c))
