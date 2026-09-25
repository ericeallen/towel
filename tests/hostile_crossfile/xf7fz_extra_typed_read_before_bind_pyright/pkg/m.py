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

def scale(x: int) -> int:
    return x * 2


def f1(xs: list[int]) -> int:
    print("start", len(xs))
    scale = scale(len(xs))
    print("after", scale)
    return scale


def f2(xs: list[int]) -> int:
    print("begin", len(xs) * 2)
    scale = scale(len(xs))
    print("after", scale)
    return scale
