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
# Gemini review of 2026-10-31, case same_named_methods_semicolon_orphan, in the
# shape that still reproduced on 1.947: the block ends at c = 3, and res = c + x
# runs after it on the same line, so the helper must return c or the pair must
# be declined. Two classes define compute, so the orphan check must use the
# method that holds each block, not the last method of that name.
class First:
    def compute(self, x):
        print("first", x)
        a = 1
        b = 2
        c = 3; res = c + x
        c = 0
        return res + a + b


class Second:
    def compute(self, x):
        print("second", x)
        a = 1
        b = 2
        c = 3; res = c * x
        c = 0
        return res - a - b


if __name__ == "__main__":
    for kind in (First, Second):
        try:
            print(kind.__name__, kind().compute(5))
        except Exception as error:
            print(kind.__name__, "raised", type(error).__name__, error)
