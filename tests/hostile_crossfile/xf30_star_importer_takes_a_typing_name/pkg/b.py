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

from collections.abc import Callable


class Any:
    def __init__(self, label: str = "any") -> None:
        self.label = label


from pkg.a import *  # noqa: E402,F403


def kind(value: object) -> str:
    match value:
        case Callable():
            return "callable"
        case _:
            return "other"


def label() -> str:
    return Any("b").label
