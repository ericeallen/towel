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

def l1(items):
    def show():
        return factor
    out = [show for _ in items]
    out.append("x")
    factor = 10
    return [o() if callable(o) else o for o in out]
def l2(items):
    def show():
        return factor
    out = [show for _ in items]
    out.append("y")
    factor = 20
    return [o() if callable(o) else o for o in out]
if __name__ == "__main__":
    print(l1([1, 2]), l2([1]))
