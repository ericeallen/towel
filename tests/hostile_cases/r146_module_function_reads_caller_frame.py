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

# ``_caller`` reads the frame above the one that calls it, the way
# typing_extensions finds a TypeAliasType's defining module. Two blocks that
# call it must stay in their functions: moved into a helper, ``_caller`` would
# see the helper's caller instead of theirs.
import sys


def _caller(depth=1):
    return sys._getframe(depth + 1).f_code.co_name


class First:
    def __init__(self, size):
        self.origin = _caller()
        self.size = size
        self.label = f"{self.origin}:{self.size}"
        self.kind = "first"


class Second:
    def __init__(self, size):
        self.origin = _caller()
        self.size = size
        self.label = f"{self.origin}:{self.size}"
        self.kind = "second"


def make_first():
    return First(1)


def make_second():
    return Second(2)


if __name__ == "__main__":
    print(make_first().label, make_second().label)
