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

# CPython captures a function's builtins mapping at definition time. A helper
# inserted before unrelated captures the original mapping instead of len=100.
def unrelated():
    pass

__builtins__ = {"len": lambda data: 100}

def first(data):
    x = len(data)
    y = x + 2
    z = y * 3
    return z

def second(data):
    x = len(data)
    y = x + 2
    z = y * 3
    return z

if __name__ == "__main__":
    print(first([1]), second([1]))
