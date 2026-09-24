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

class Test:
    def flakes(self, *sources):
        return [len(s) for s in sources]
    def test_a(self):
        r = self.flakes("import a")
        r += self.flakes("import a; a")
        r += self.flakes("import a as b")
        return r
    def test_b(self):
        r = self.flakes("from a import b")
        r += self.flakes("from a import b; b")
        r += self.flakes("from a import b as c")
        return r
class TestOther:
    def flakes(self, *sources):
        return [2 * len(s) for s in sources]
    def test_c(self):
        r = self.flakes("x = 1")
        r += self.flakes("x = 1; x")
        r += self.flakes("y = 2")
        return r
def free_function(flakes):
    r = flakes("z")
    r += flakes("zz")
    r += flakes("zzz")
    return r
if __name__ == "__main__":
    print(Test().test_a(), Test().test_b(), TestOther().test_c(), free_function(TestOther().flakes))
