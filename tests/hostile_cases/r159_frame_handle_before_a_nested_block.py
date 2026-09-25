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

# The frame handle is taken before the shared block, in the same loop body, and
# read after it: ``f_locals`` lists the block's names. Moved into a helper,
# those names are the helper's, and the list comes back shorter. The check for
# frame reads outside a block stopped walking the loop at the block and never
# saw the handle.
import sys


def first(items):
    names = []
    for item in items:
        frame = sys._getframe()
        doubled = item * 2
        label = "first" + str(doubled)
        print(label, len(label))
        names.append(sorted(frame.f_locals))
    return names


def second(items):
    names = []
    for item in items:
        frame = sys._getframe()
        doubled = item * 2
        label = "second" + str(doubled)
        print(label, len(label))
        names.append(sorted(frame.f_locals))
    return names


if __name__ == "__main__":
    print(first([1]), second([3]))
