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

"""Data models for the unification-based refactoring system.

This module defines the core data structures used throughout the refactoring engine:
- Code block pairs for comparison
- Method information for class context
- Refactoring proposals
- Function context for analysis
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Literal, Optional, Tuple, Union
import ast


@dataclass
class CodeBlockPair:
    """Represents a pair of potentially duplicate code blocks."""

    file_path: str
    function1_name: str
    function2_name: str
    block1_range: Tuple[int, int]
    block2_range: Tuple[int, int]
    block1_nodes: List[ast.AST]
    block2_nodes: List[ast.AST]
    file_path2: Optional[str] = None
    class1_name: Optional[str] = None
    class2_name: Optional[str] = None
    enclosing_function1_name: Optional[str] = None
    enclosing_function2_name: Optional[str] = None
    function1_ancestry: Optional[List[str]] = None
    function2_ancestry: Optional[List[str]] = None
    scope_analyzer1: Optional["ScopeAnalyzer"] = None
    scope_analyzer2: Optional["ScopeAnalyzer"] = None
    root_scope1: Optional["Scope"] = None
    root_scope2: Optional["Scope"] = None
    source1: Optional[str] = None
    source2: Optional[str] = None
    function1_node: Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]] = None
    function2_node: Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]] = None


@dataclass
class MethodInfo:
    """Describes how a function participates as a method within a class."""

    kind: Optional[Literal["instance", "classmethod", "staticmethod"]]
    implicit_param: Optional[str]


@dataclass
class ClassInfo:
    """Summarizes class definitions discovered during analysis."""

    name: str
    qualname: str
    file_path: str
    bases: List[str]


@dataclass
class ClassInsertionPlan:
    """Describes where an extracted helper should be inserted within a class hierarchy."""

    class_name: str
    file_path: str
    method_kind: Literal["instance", "classmethod", "staticmethod"]
    implicit_param: Optional[str]


@dataclass
class Replacement:
    """Represents a replacement call to the extracted function/method."""

    line_range: Tuple[int, int]
    node: ast.AST
    file_path: Optional[str] = None
    class_name: Optional[str] = None
    method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
    implicit_param: Optional[str] = None


@dataclass
class RefactoringProposal:
    """Proposed refactoring."""

    file_path: str
    extracted_function: ast.FunctionDef
    replacements: List[Replacement]
    description: str
    parameters_count: int
    return_variables: List[str] = field(default_factory=list)
    insert_into_class: Optional[str] = None
    insert_into_function: Optional[str] = None
    method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
    method_param_name: Optional[str] = None

    def __post_init__(self) -> None:
        """Coerce legacy tuple replacements into :class:`Replacement` instances."""

        coerced: List[Replacement] = []
        for item in self.replacements:
            if isinstance(item, Replacement):
                coerced.append(item)
                continue

            if not isinstance(item, tuple):
                raise TypeError(
                    "Replacement entries must be Replacement instances or tuples, "
                    f"got {type(item)!r}"
                )

            if len(item) == 4:
                line_range, node, file_path, class_name = item
            elif len(item) == 3:
                line_range, node, file_path = item
                class_name = None
            elif len(item) == 2:
                line_range, node = item
                file_path = None
                class_name = None
            else:
                raise ValueError(
                    "Replacement tuple must have length 2, 3, or 4 ("
                    "line_range, node[, file_path[, class_name]])"
                )

            method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
            implicit_param: Optional[str] = None
            if class_name is not None:
                method_kind = "instance"
                implicit_param = "self"

            coerced.append(
                Replacement(
                    line_range=line_range,
                    node=node,
                    file_path=file_path,
                    class_name=class_name,
                    method_kind=method_kind,
                    implicit_param=implicit_param,
                )
            )

        self.replacements = coerced


@dataclass
class ParsedModule:
    """Container for parsed module data flowing through the pipeline."""

    file_path: str
    source: str
    tree: ast.AST
    scope_analyzer: Optional["ScopeAnalyzer"] = None
    root_scope: Optional["Scope"] = None
    class_infos: List[ClassInfo] = field(default_factory=list)


@dataclass
class FunctionArtifact:
    """Represents a function (sync or async) discovered during analysis with context."""

    file_path: str
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    source: str
    scope_analyzer: "ScopeAnalyzer"
    root_scope: "Scope"
    class_name: Optional[str]
    enclosing_function: Optional[str]
    ancestry: List[str]


if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from .scope_analyzer import ScopeAnalyzer, Scope
