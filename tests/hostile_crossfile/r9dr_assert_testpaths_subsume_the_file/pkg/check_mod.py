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

from pkg import helpers_x


def check_here(values, offset):
    total = sum(values) + offset
    doubled = total * 2
    assert doubled < 20, "too big"
    print("checked", total, doubled)
    return doubled


def test_here():
    check_here([5, 6], 1)


def test_there():
    helpers_x.check_there([5, 6], 1)
