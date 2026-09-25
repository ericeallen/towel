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

# A stand-in for inline-snapshot 0.35.4, as read in its source: snapshot() keys
# each call by its position in the caller's frame, (id(f_code), f_lasti), as
# inline_snapshot._types.key_for does, and a position that sees a second value
# raises UsageError, as the library does under pytest.
import sys


class UsageError(Exception):
    pass


_SEEN = {}


def snapshot(value):
    frame = sys._getframe(1)
    key = (id(frame.f_code), frame.f_lasti)
    if key in _SEEN and _SEEN[key] != value:
        raise UsageError("snapshot value should not change. Use Is(...) for dynamic snapshot parts.")
    _SEEN[key] = value
    return value
