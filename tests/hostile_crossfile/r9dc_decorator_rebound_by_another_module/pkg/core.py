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

# parse_a's decorator is a plain wrapper where it is defined, but
# enable_checks.py rebinds pkg.checks.checked before this module is imported,
# so parse_a is recompiled with a trace after every assignment: a block moved
# out of it into a helper would no longer be traced (round-4 audit, P1-07).
from pkg.checks import checked


@checked
def parse_a(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


def parse_b(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5
