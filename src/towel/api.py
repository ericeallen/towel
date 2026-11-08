# Copyright 2026 Eric Allen
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Supported library entry points; see docs/COMPATIBILITY.md for their contract.

These are the engine's actual types and functions, not a separate implementation.
Treat analyzed proposals as opaque snapshots and pass them back to the engine
that produced them. Change plans expose immutable byte changes for review.
"""

from .changes import (
    ChangeConflict,
    ChangePlan,
    ExternalEdit,
    FileChange,
    RecoveryRequired,
    StaleSource,
    apply_changes,
)
from .unification.exceptions import RefactoringError, TowelError
from .unification.models import RefactoringProposal
from .unification.refactor_engine import UnificationRefactorEngine

__all__ = [
    "ChangeConflict",
    "ChangePlan",
    "ExternalEdit",
    "FileChange",
    "RecoveryRequired",
    "RefactoringError",
    "RefactoringProposal",
    "StaleSource",
    "TowelError",
    "UnificationRefactorEngine",
    "apply_changes",
]
