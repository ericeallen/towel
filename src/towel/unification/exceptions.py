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

"""Custom exceptions for the Towel refactoring system.

This module defines domain-specific exceptions that provide clearer error
semantics than generic Python exceptions.
"""

from enum import StrEnum


class TowelError(Exception):
    """Base exception for all Towel-related errors."""


class RefactoringError(TowelError):
    """Failed to apply a refactoring transformation.

    Raised when a proposal cannot be rendered into the file it targets, for
    example when the class it should be inserted into is not unique.
    """


class CheckerUnavailableError(RefactoringError):
    """The type checker could not judge a candidate: it timed out, crashed, or could not start.

    Distinct from a candidate the checker refused, so a run whose every
    candidate went unjudged is reported as that rather than as a fixed point.
    """


class Untypeable(StrEnum):
    """What an extraction takes from its callers that no signature of the helper gives back.

    Each is the reason a declined proposal is reported under, in the words the
    run's summary uses.
    """

    NARROWING_READ_AFTER_CALL = "narrows what its caller reads after the call"
    """The block tests a name or attribute that the code after it relies on (packaging's
    ``if self._key_cache is None``); the narrowing ends with the helper."""
    NARROWING_READ_IN_THUNK = "narrows what a call-site lambda reads"
    """A test passed as an argument once governed an expression now passed as a lambda
    (rich's ``task.total is not None`` and ``lambda: int(task.total)``)."""
    ATTRIBUTE_DECLARATIONS = "declares its class's attributes"
    """Assignments through a method's receiver were the class's attribute declarations
    (nox's ``self.location_name = location``); a function outside the class declares none."""
    PARTIAL_TYPE = "completes its caller's partial type"
    """mypy learns an empty collection's element type from the next statement that fills
    it (mistune's ``attrs = {}``); passed to a call first, it is an error at the assignment."""


class UntypeableExtraction(RefactoringError):
    """A proposal no signature of its helper can type, and which of those reasons it is.

    The ladder raises it instead of checking its remaining rungs, as soon as the
    reason is known: from the proposal alone where that is enough, else from the
    first refusal that shows it. The proposal is reported under the reason
    rather than as an ordinary refusal.
    """

    def __init__(self, reason: Untypeable, detail: str) -> None:
        super().__init__(f"No helper signature can type this extraction: it {reason}: {detail}")
        self.reason = reason
        self.detail = detail


class UnsupportedLayoutError(TowelError, ValueError):
    """The project's packaging layout is one Towel does not model.

    Nothing raises it since 1.772: module names come from the program's own
    imports (``towel.import_model``), and no packaging layout is read. It is
    kept so that callers catching it keep working, and will be removed in a
    later release. Still a ``ValueError`` for callers that catch that.
    """


class AmbiguousImportsError(TowelError):
    """The program's imports do not name the modules of the refactoring target unambiguously.

    A helper shared across modules is imported by the name the program's own
    imports give its host, so a run that may share one refuses before it
    writes anything when those imports leave the target's names in doubt.
    """


class ProjectScanLimitError(TowelError):
    """The project around the input is too large to read whole for what it already names.

    Not a ``RefactoringError``: that declines one proposal and the run goes on,
    whereas no proposal can be named safely here, so the run stops and says so.
    """
