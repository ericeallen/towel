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

# The plugins must load in this order: zeta overrides alpha.
from pkg import zeta  # noqa: F401
from pkg import alpha  # noqa: F401
from pkg.registry import PLUGINS


def summarize(rows):
    total = 0
    for row in rows:
        if row > 0:
            total += row * 2
    print("summarize", total, PLUGINS[0])
    return total


def summarize_again(rows):
    total = 0
    for row in rows:
        if row > 0:
            total += row * 2
    print("summarize", total, PLUGINS[0])
    return total
