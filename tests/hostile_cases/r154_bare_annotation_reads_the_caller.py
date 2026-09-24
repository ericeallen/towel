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

# `x: int` without a value declares x but assigns nothing: the x read after
# it is still the one the caller bound. Counted as a binding, it was never
# passed, and the helper raised UnboundLocalError.
def annotated_first(v):
    x = v + 1
    x: int
    y = x * 2
    print(y, "one")
    return y
def annotated_second(v):
    w = [v]
    x = w[0] * 3
    x: int
    y = x * 2
    print(y, "two")
    return y
if __name__ == "__main__":
    print(annotated_first(1), annotated_second(2))
