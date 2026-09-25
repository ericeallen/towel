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

def e1(base):
    scale = base * 2
    def f(v):
        return v * scale
    result = [f(1), f(2)]
    scale = 0
    return result, f(3)
def e2(base):
    scale = base * 3
    def f(v):
        return v * scale
    result = [f(1), f(2)]
    scale = 0
    return result, f(3)
if __name__ == "__main__":
    print(e1(1), e2(1))
