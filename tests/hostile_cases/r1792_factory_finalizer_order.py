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
    def __init__(self, name: str) -> None:
        self.name = name
    def __del__(self) -> None:
        events.append(self.name)
def acquire(name: str) -> Token:
    return Token(name)
def first(value: int) -> int:
    z = acquire("z")
    a = acquire("a")
    p = value + 1
    q = p * 2
    events.append(q)
    return len(events)
def second(value: int) -> tuple[int | str, ...]:
    z = acquire("z")
    a = acquire("a")
    p = value + 2
    q = p * 3
    events.append(q)
    return tuple(events)
print(first(1), second(1), events)
