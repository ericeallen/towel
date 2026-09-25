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


class CheckerCannotCheckTheProject(TowelError):
    """The type checker fails on the project as it stands, with no candidate applied.

    Every candidate's check would fail the same way, so none can be judged.
    Declining each as not judged spent minutes and then exited 1 (sqlmodel ran
    893 s): the run stops at the first such failure instead, naming it. Not a
    ``RefactoringError``, which declines one proposal and lets the run go on.
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
    UNANNOTATED_IN_ANNOTATED_MODULE = "would be the one unannotated function of its module"
    """Every annotated signature was refused and only the unannotated helper is left, in a
    module whose every function is annotated: a checker that skips unannotated bodies
    accepts it, and the project's stricter CI does not (idna, whose CI runs
    ``mypy --strict`` with no configuration Towel could read)."""


class UntypeableExtraction(RefactoringError):
    """A proposal no signature of its helper can type, and which of those reasons it is.

    The ladder raises it instead of checking its remaining rungs, as soon as the
    reason is known: from the proposal alone where that is enough, else from the
    first refusal that shows it. The proposal is reported under the reason
    rather than as an ordinary refusal.
    """

    def __init__(self, reason: Untypeable, detail: str) -> None:
        lead = (
            "No annotated helper signature types this extraction, and an unannotated helper"
            if reason is Untypeable.UNANNOTATED_IN_ANNOTATED_MODULE
            else "No helper signature can type this extraction: it"
        )
        super().__init__(f"{lead} {reason}: {detail}")
        self.reason = reason
        self.detail = detail


class UnverifiableChangeError(RefactoringError):
    """A candidate would change a file where the type checker cannot see what it moves.

    The original check leaves a name there that it cannot type -- an import it
    cannot resolve or finds no types for, a decorator without types -- and
    everything that name reaches is ``Any``, which accepts every use. No check
    could reject a change that misuses it, so the candidate is declined, not
    refused on its merits.
    """


class UncheckedCodeError(UnverifiableChangeError):
    """A candidate would change code the type checker does not look at.

    The checker takes it to be unreachable, and reports nothing there, so its
    acceptance of the change said nothing. That may be for the platform and
    Python version it checks for -- a module that asserts another platform, a
    branch under a ``sys.platform`` or ``sys.version_info`` test it makes
    false -- which the project's own check may look at on another platform,
    and did at trio. Or the declared types may rule the code out on every
    platform: packaging's ``return NotImplemented`` after an ``isinstance``
    test that an argument's annotation always passes.
    """


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
