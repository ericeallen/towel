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


class UnsupportedLayoutError(TowelError, ValueError):
    """The project's packaging layout is one Towel does not model.

    Raised by layout discovery for a configuration whose import names it
    cannot infer safely. Still a ``ValueError`` for callers that catch that.
    """
