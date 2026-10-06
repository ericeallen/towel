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

events: list[str] = []
class Ticket:
    def __init__(self, label: str) -> None:
        self.label = label
    def __del__(self) -> None:
        events.append('drop:' + self.label)
def make(label: str) -> Ticket:
    return Ticket(label)
def fail() -> None:
    raise ValueError('stopped')
def first() -> None:
    try:
        resource = make('first')
        label = resource.label
        lower = label.lower()
        events.append(lower)
        fail()
    except ValueError:
        events.append('caught:first')
    events.append('continue:first')
def second() -> None:
    try:
        resource = make('second')
        label = resource.label
        lower = label.lower()
        events.append(lower)
        fail()
    except ValueError:
        events.append('caught:second')
    for marker in ('continue:second',):
        events.append(marker)
first()
second()
print(events)
