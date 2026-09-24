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

def z1(items):
    factor = 2
    def scale(v):
        return v * factor
    factor = 3
    return [scale(i) for i in items]
def z2(items):
    factor = 2
    def scale(v):
        return v * factor
    factor = 4
    return [scale(i) for i in items]
if __name__ == "__main__":
    print(z1([1]), z2([1]))
