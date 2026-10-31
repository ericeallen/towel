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
class Owner:
    def __init__(self, label: str) -> None:
        self.label = label
    @property
    def token(self) -> Ticket:
        return Ticket(self.label)
    def __getitem__(self, key: int) -> Ticket:
        return Ticket(self.label)
    def __add__(self, value: int) -> Ticket:
        return Ticket(self.label)
    def obtain(self) -> Ticket:
        return Ticket(self.label)
def first(provider: Owner) -> int:
    resource = provider.token
    label = resource.label
    lower = label.lower()
    total = len(lower)
    events.append('continue:first')
    return total
def second(provider: Owner) -> int:
    resource = provider.token
    label = resource.label
    lower = label.lower()
    total = len(lower)
    for marker in ('continue:second',):
        events.append(marker)
    return total
print(first(Owner('first')), second(Owner('second')), events)
