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
import os
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


def python_tool_environment() -> dict[str, str]:
    """Keep child tools independent of caller-supplied Python import/startup settings.

    Isolated interpreter flags protect module launches; a tool discovered on
    PATH may instead be a Python console script whose shebang we cannot change.
    Neither path should load project code through PYTHONPATH or sitecustomize.
    """
    return {
        **{name: value for name, value in os.environ.items() if not name.startswith("PYTHON")},
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
    }


@dataclass(frozen=True)
class ToolChoice(Generic[T]):
    """What ``*_for_project`` chose, and why.

    ``tool`` is None when nothing suitable is installed. ``note`` names the
    choice ("Black", "ruff import sorting", "mypy") and any configured tool
    that is missing, so the command line can tell the user what happened.
    """

    tool: Optional[T]
    note: str
