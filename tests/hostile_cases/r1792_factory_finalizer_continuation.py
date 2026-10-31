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

events: list[int | str] = []
class Token:
    def __del__(self) -> None:
        events.append("closed")
def acquire() -> Token:
    return Token()
def a(x: int) -> int:
    token = acquire()
    y = x + 1
    z = y * 2
    events.append(z)
    return len(events)
def b(x: int) -> tuple[int | str, ...]:
    token = acquire()
    y = x + 2
    z = y * 3
    events.append(z)
    return tuple(events)
print(a(1), b(1), events)
