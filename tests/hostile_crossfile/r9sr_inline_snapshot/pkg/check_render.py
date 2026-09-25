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

# The audit's reproducer (round 4, rich-click): the two literals must stay in
# their own snapshot() calls, and the code before them may still be shared.
from inline_snapshot import snapshot


def render(n):
    return f"<item {n}>"


def test_one():
    out = render(1)
    assert out.startswith("<item")
    assert out.endswith(">")
    assert out == snapshot("<item 1>")


def test_two():
    out = render(2)
    assert out.startswith("<item")
    assert out.endswith(">")
    assert out == snapshot("<item 2>")
