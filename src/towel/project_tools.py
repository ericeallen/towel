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

"""The result of choosing an external tool from a project's configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ToolChoice(Generic[T]):
    """What ``*_for_project`` chose, and why.

    ``tool`` is None when nothing suitable is installed. ``note`` names the
    choice ("Black", "ruff import sorting", "mypy") and any configured tool
    that is missing, so the command line can tell the user what happened.
    """

    tool: Optional[T]
    note: str
