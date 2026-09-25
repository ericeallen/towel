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

# Only this module rebinds ``len``. Whichever pass pairs this block with
# the others, or with the helper they share, is declined; a.py and b.py
# still share theirs.
from pkg.util import len


def fc(values):
    print("pre", "c")
    size = len(values) if hasattr(values, "__len__") else values
    size = size * 2
    print("post", size)
    return size
