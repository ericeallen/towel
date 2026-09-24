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

class lazyclassproperty:
    def __init__(self, fn): self.fn = fn
    def __get__(self, obj, cls): return self.fn(cls)
class Chars:
    letters = "ba"
    digits = "21"
    @lazyclassproperty
    def ident(cls):
        return "".join(sorted(set(cls.letters) | set("xy_")))
    @lazyclassproperty
    def body(cls):
        return "".join(sorted(set(cls.digits) | set("0·")))
    @lazyclassproperty
    def both(cls):
        return "".join(sorted(set(cls.letters) | set("q")))
if __name__ == "__main__":
    print(Chars.ident, Chars.body, Chars.both, Chars().ident)
