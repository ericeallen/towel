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


class UnverifiableChangeError(RefactoringError):
    """A candidate would change a file where the type checker cannot see what it moves.

    The original check leaves a name there that it cannot type -- an import it
    cannot resolve or finds no types for, a decorator without types -- and
    everything that name reaches is ``Any``, which accepts every use. No check
    could reject a change that misuses it, so the candidate is declined, not
    refused on its merits.
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
