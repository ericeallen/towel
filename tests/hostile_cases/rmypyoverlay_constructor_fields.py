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

"""Constructor attributes must remain visible to unchanged typed consumers."""


class Literal:
    def __init__(self, match_string: str) -> None:
        self.match = match_string
        self.matchLen = len(match_string)


class Keyword:
    def __init__(self, match_string: str) -> None:
        self.match = match_string
        self.matchLen = len(match_string)


def consumer(value: Literal | Keyword) -> tuple[str, int]:
    return value.match, value.matchLen


def first(value: int) -> int:
    scaled = value * 2
    label = str(scaled)
    return len(label) + scaled


def second(value: int) -> int:
    scaled = value * 3
    label = str(scaled)
    return len(label) + scaled
