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

def first_positive(values):
    if (found := next((v for v in values if v > 0), None)) is not None:
        print("found", found)
        return found * 2
    print("none")
    return None
def first_negative(values):
    if (hit := next((v for v in values if v > 0), None)) is not None:
        print("found", hit)
        return hit * 2
    print("none")
    return None
if __name__ == "__main__":
    print(first_positive([-1, 3, 5]), first_negative([0, 0]), first_negative([7]))
