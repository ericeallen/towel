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

"""Data models for the unification-based refactoring system.

This module defines the core data structures used throughout the refactoring engine:
- Code block pairs for comparison
- Method information for class context
- Refactoring proposals
- Function context for analysis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Dict, List, Literal, NamedTuple, Optional, Set, Tuple, Union
import ast
import hashlib


@dataclass
class CodeBlockPair:
    """Represents a pair of potentially duplicate code blocks."""

    file_path: str
    function1_name: str
    function2_name: str
    block1_range: Tuple[int, int]
    block2_range: Tuple[int, int]
    block1_nodes: List[ast.stmt]
    block2_nodes: List[ast.stmt]
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
    # False when a decorator or special method name makes the receiver's
    # meaning unknowable statically; such blocks get module-level helpers.
    receiver_known: bool = True


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


@dataclass(frozen=True)
class ReusedFunction:
    """An existing module-level function that one duplicate site is the whole body of.

    The proposal leaves this function untouched and rewrites the other sites to
    call it, so no helper is emitted. ``line_range`` spans the definition so
    overlap filtering keeps competing proposals from editing it underneath.
    """

    name: str
    file_path: str
    line_range: Tuple[int, int]


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
    source_digests: Tuple[Tuple[str, str], ...] = ()
    # When set, ``extracted_function`` is that existing definition (for display)
    # and every replacement already calls it by name; nothing is inserted.
    reused_function: Optional[ReusedFunction] = None
    # The call sites declare types, so parameters the copied annotations left
    # bare may be filled by a type inferrer when the proposal is applied.
    wants_type_inference: bool = False
    # ``(module, name)`` pairs an inferred annotation needs imported into the
    # helper's module, such as ``typing.Any``; filled when the proposal is applied.
    required_imports: Tuple[Tuple[str, str], ...] = ()


@dataclass
class ParsedModule:
    """Container for parsed module data flowing through the pipeline."""

    file_path: str
    source: str
    tree: ast.AST
    scope_analyzer: Optional["ScopeAnalyzer"] = None
    root_scope: Optional["Scope"] = None
    class_infos: List[ClassInfo] = field(default_factory=list)
    source_digest: str = field(init=False, repr=False, compare=False)
    """SHA-256 of ``source``, computed once here for every consumer of the module."""

    def __post_init__(self) -> None:
        self.source_digest = source_digest_of(self.source)


def source_digest_of(source: str) -> str:
    """The SHA-256 hex digest of a module's source text."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class FunctionArtifact(NamedTuple):
    """A function (sync or async) discovered during analysis, with its context.

    A NamedTuple so it can be passed straight into the engine's positional
    consumers while still offering named-field access; field order is the
    contract those consumers unpack.
    """

    file_path: str
    node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    source: str
    scope_analyzer: "ScopeAnalyzer"
    root_scope: "Scope"
    class_name: Optional[str]
    enclosing_function: Optional[str]
    ancestry: List[str]
    source_digest: str = ""
    """SHA-256 of ``source`` when known; empty when the artifact was built by hand."""

    @property
    def module_digest(self) -> str:
        """The digest of the module source, computed here only when the pipeline did not."""
        return self.source_digest or source_digest_of(self.source)


if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from .scope_analyzer import ScopeAnalyzer, Scope


FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
"""A function definition of either kind."""

ParameterKind = Literal["value", "thunk", "lifted", "receiver"]
"""How a helper parameter receives its argument: a plain value, a zero-argument
thunk called where the expression stood, a lambda over block-local names, or
the bound receiver of a method helper."""


class RejectReason(StrEnum):
    """Why a candidate pair was declined; the vocabulary of the rejection trace."""

    CLOSURE_CROSSES_BLOCK_BOUNDARY = "closure_crosses_block_boundary"
    CONDITIONALLY_BOUND_RETURN = "conditionally_bound_return"
    CROSS_MODULE_GLOBAL_DECLARATION = "cross_module_global_declaration"
    FRAME_SENSITIVE_BLOCK = "frame_sensitive_block"
    IMPORT_CYCLE = "import_cycle"
    UNKNOWN_LAYOUT = "unknown_layout"
    IMPURE_EAGER_PARAMETER = "impure_eager_parameter"
    INCOMPLETE_LIFETIME_BLOCK1 = "incomplete_lifetime_block1"
    INCOMPLETE_LIFETIME_BLOCK2 = "incomplete_lifetime_block2"
    INCOMPLETE_RETURN_COVERAGE_BLOCK1 = "incomplete_return_coverage_block1"
    INCOMPLETE_RETURN_COVERAGE_BLOCK2 = "incomplete_return_coverage_block2"
    INSTANTIATION_MISMATCH = "instantiation_mismatch"
    MIXED_RETURN_AND_VARIABLES = "mixed_return_and_variables"
    MODULE_DATA_LOOKUP = "module_data_lookup"
    MOVES_SCOPE_DECLARATION = "moves_scope_declaration"
    NESTED_BINDING_ESCAPES = "nested_binding_escapes"
    NONLOCAL_SAFETY_SKIP = "nonlocal_safety_skip"
    NOT_STRUCTURALLY_SIMILAR = "not_structurally_similar"
    ORPHANED_VARIABLES = "orphaned_variables"
    PRIVATE_NAME_LEXICAL_CLASS = "private_name_lexical_class"
    REBOUND_EXTERNAL_BINDING = "rebound_external_binding"
    RETURN_VARIABLES_NOT_ALIGNED = "return_variables_not_aligned"
    TRIVIAL_FORWARDING_HELPER = "trivial_forwarding_helper"
    TRIVIAL_RETURN_BLOCKS = "trivial_return_blocks"
    UNBINDS_EXTERNAL_NAME = "unbinds_external_name"
    UNDEFINED_NAMES_IN_CALL = "undefined_names_in_call"
    UNIFICATION_FAILED = "unification_failed"
    UNSAFE_REASSIGNMENT_BLOCK1 = "unsafe_reassignment_block1"
    UNSAFE_REASSIGNMENT_BLOCK2 = "unsafe_reassignment_block2"
    VALUE_PRODUCING_MISMATCH = "value_producing_mismatch"


@dataclass(frozen=True)
class AppliedChange:
    """One call site rewritten by an applied refactoring: what stood there and what replaced it."""

    helper: str
    path: str
    line: int
    before: str
    after: str


@dataclass(frozen=True)
class BlockBindingSnapshot:
    """Summarized binding data for a block: what it binds, reassigns, and what is bound around it."""

    bound_in_block: Set[str]
    reassigned_in_block: Set[str]
    bound_before_block: Set[str]
    bound_after_block: Set[str]
    initially_bound: Set[str]


@dataclass(frozen=True)
class HelperTemplate:
    """The template helper a clustered occurrence must reproduce to reuse it.

    These values are fixed for a given (pair, extracted helper) and are shared
    across every candidate occurrence tested against that helper.
    """

    pair: CodeBlockPair
    func_def: ast.FunctionDef
    func_def_dump: str
    param_order: Dict[str, int]
    preamble_length: int
    free_vars: Set[str]
    enclosing_names: Set[str]
    is_value_producing: bool
    globals_to_declare: Set[str]
    nonlocals_to_declare: Set[str]


def encloses(outer: FunctionNode, inner: FunctionNode) -> bool:
    """Whether ``inner`` is ``outer`` or lies within its source span."""
    if outer is inner:
        return True
    outer_end = outer.end_lineno or outer.lineno
    inner_end = inner.end_lineno or inner.lineno
    return outer.lineno <= inner.lineno and inner_end <= outer_end and outer is not inner
