# Copyright 2025 Eric Allen
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

    pass


class UnificationError(TowelError):
    """Failed to unify two code blocks.

    Raised when the unification algorithm cannot find a valid substitution
    that makes two AST structures equivalent.
    """

    pass


class ExtractionError(TowelError):
    """Failed to extract a function from a code block.

    Raised when function extraction fails due to invalid AST structure,
    scope issues, or other extraction constraints.
    """

    pass


class OrphanVariableError(ExtractionError):
    """Code block contains orphaned variables.

    Raised when attempting to extract code that would leave variables
    in an undefined state. For example, extracting code that binds a
    variable but leaving usage of that variable in the original scope.

    Example:
        >>> # This would raise OrphanVariableError:
        >>> # Extract lines 1-2, leaving line 3:
        >>> x = 10
        >>> y = 20
        >>> total = x + y  # Would be orphaned
    """

    pass


class ScopeAnalysisError(TowelError):
    """Error during scope analysis.

    Raised when scope analysis encounters unexpected AST structures
    or fails to determine variable binding relationships.
    """

    pass


class RefactoringError(TowelError):
    """Failed to apply a refactoring transformation.

    Raised when applying a refactoring proposal fails, such as when
    generating replacement code or writing modified files.
    """

    pass


class ParseError(TowelError):
    """Failed to parse Python source code.

    Raised when AST parsing fails due to syntax errors or unsupported
    Python constructs.
    """

    pass


class ImportResolutionError(TowelError):
    """Failed to resolve an import path.

    Raised during cross-file refactoring when import paths cannot be
    determined or when module structure is ambiguous.
    """

    pass


class ClassHierarchyError(TowelError):
    """Error in class hierarchy analysis.

    Raised when analyzing class inheritance relationships for method
    promotion into shared base classes.
    """

    pass
