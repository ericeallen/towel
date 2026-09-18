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

"""
Unification-based code refactoring.

This module implements a principled approach to detecting and extracting
duplicate code using unification from automated theorem proving.

``UnificationRefactorEngine`` is imported on first use, so importing one
analysis module (the unifier, the scope analyzer) does not load the engine
and everything it depends on.
"""

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .refactor_engine import UnificationRefactorEngine

__all__: List[str] = ["UnificationRefactorEngine"]


def __getattr__(name: str) -> object:
    if name == "UnificationRefactorEngine":
        from .refactor_engine import UnificationRefactorEngine

        return UnificationRefactorEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
