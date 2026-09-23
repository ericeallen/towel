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

"""Data models for the anti-unification refactoring system: parsed and
analyzed modules, functions and classes with their context, candidate block
pairs, the helper template and clustered-site records, replacements and
proposals (including a redirect to an existing function), applied changes,
and the typed reasons a pair is declined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import (
    TYPE_CHECKING,
    Dict,
    List,
    Literal,
    NamedTuple,
    Optional,
    Set,
    Tuple,
    Union,
    FrozenSet,
    Hashable,
)
import ast
import hashlib
import re

MethodKind = Literal["instance", "classmethod", "staticmethod"]
"""How a helper placed in a class binds its receiver."""


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
    file_path2: str
    # The class and enclosing function of each block's function, when it has them.
    class1_name: Optional[str]
    class2_name: Optional[str]
    enclosing_function1_name: Optional[str]
    enclosing_function2_name: Optional[str]
    function1_ancestry: List[str]
    function2_ancestry: List[str]
    scope_analyzer1: "ScopeAnalyzer"
    scope_analyzer2: "ScopeAnalyzer"
    root_scope1: "Scope"
    root_scope2: "Scope"
    source1: str
    source2: str
    function1_node: "FunctionNode"
    function2_node: "FunctionNode"

    @property
    def is_cross_file(self) -> bool:
        """Whether the two blocks live in different files."""
        return self.file_path2 != self.file_path


@dataclass
class MethodInfo:
    """Describes how a function participates as a method within a class."""

    kind: Optional[MethodKind]
    implicit_param: Optional[str]
    # False when a decorator or special method name makes the receiver's
    # meaning unknowable statically; such blocks get module-level helpers.
    receiver_known: bool = True


@dataclass
class ClassInfo:
    """A class statement found during analysis: its name, dotted qualname, and file."""

    name: str
    qualname: str
    file_path: str


@dataclass
class ClassInsertionPlan:
    """The class holding both duplicates that takes the helper, and how it binds the receiver."""

    class_name: str
    file_path: str
    method_kind: MethodKind
    implicit_param: Optional[str]


@dataclass(frozen=True)
class ClusterContext:
    """Where a clustered call site sits: its class, and how its function binds a receiver."""

    class_name: Optional[str]
    method: MethodInfo


@dataclass(frozen=True)
class HelperHome:
    """Where a helper is placed: the file, and the class or function within it that hosts it.

    ``method_param_name`` is the helper's own receiver parameter when it
    becomes a method; a call site's ``implicit_param`` (see
    ``Replacement``) is the receiver name in the method that hosts the call.
    """

    file_path: str
    insert_into_class: Optional[str]
    insert_into_function: Optional[str]
    method_kind: Optional[MethodKind]
    method_param_name: Optional[str]


@dataclass
class Replacement:
    """Represents a replacement call to the extracted function/method."""

    line_range: Tuple[int, int]
    node: ast.stmt
    file_path: Optional[str] = None
    class_name: Optional[str] = None
    method_kind: Optional[MethodKind] = None
    implicit_param: Optional[str] = None


GENERATED_HELPER_NAME = re.compile(r"_{1,2}extracted_func(?:_\d+)?")
"""The names the materializer gives helpers, until ``rename-helpers`` gives them meaning."""


def is_generated_helper_name(name: str) -> bool:
    """Whether ``name`` is one this tool gave a helper it inserted."""
    return GENERATED_HELPER_NAME.fullmatch(name) is not None


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
    method_kind: Optional[MethodKind] = None
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
    # ``(module, name)`` pairs an annotation needs that must not run at import
    # time: the helper's module may not import them without closing a cycle,
    # and a name wanted only by an annotation never needs to exist at runtime.
    type_checking_imports: Tuple[Tuple[str, str], ...] = ()
    # Imports and type-variable declarations belonging to this annotation
    # variant. Rendered before a fresh module helper or the host of a method;
    # annotation fallbacks must drop this preamble along with its annotations.
    helper_type_declarations: Tuple[ast.stmt, ...] = ()


@dataclass
class RawModule:
    """A module read and parsed, before scope analysis."""

    file_path: str
    source: str
    tree: ast.AST


@dataclass
class ParsedModule(RawModule):
    """A module with its scopes analyzed: what the pipeline's later phases consume."""

    scope_analyzer: "ScopeAnalyzer"
    root_scope: "Scope"
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


if TYPE_CHECKING:
    from .scope_analyzer import ScopeAnalyzer, Scope


FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]
"""A function definition of either kind."""

TerminationReason = Literal["fixed_point", "iteration_cap"]
"""Why a fixed-point driver stopped: nothing left to apply, or the cap was reached."""

ParameterKind = Literal["value", "thunk", "lifted", "receiver"]
"""How a helper parameter receives its argument: a plain value, a zero-argument
thunk called where the expression stood, a lambda over block-local names, or
the bound receiver of a method helper."""


class RejectReason(StrEnum):
    """Why a candidate pair was declined; the vocabulary of the rejection trace."""

    BARE_NAME_DIFFERS_BY_MODULE = "bare_name_differs_by_module"
    BUILTIN_MAY_DIFFER_BY_MODULE = "builtin_may_differ_by_module"
    CLOSURE_CROSSES_BLOCK_BOUNDARY = "closure_crosses_block_boundary"
    CONDITIONALLY_BOUND_RETURN = "conditionally_bound_return"
    CREATED_OBJECT_ESCAPES = "created_object_escapes"
    CROSS_MODULE_GLOBAL_DECLARATION = "cross_module_global_declaration"
    EXISTING_HELPER_BECOMES_FORWARDER = "existing_helper_becomes_forwarder"
    FRAME_READ_IN_FUNCTION = "frame_read_in_function"
    FRAME_SENSITIVE_BLOCK = "frame_sensitive_block"
    HOST_HAS_STUB = "host_has_stub"
    IMPORT_CYCLE = "import_cycle"
    IMPORT_TIME_EFFECTS = "import_time_effects"
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
    NARROWING_LOST_AT_CALL_SITE = "narrowing_lost_at_call_site"
    NEEDS_CLASS_BODY = "needs_class_body"
    NESTED_BINDING_ESCAPES = "nested_binding_escapes"
    NEW_IMPORT_REQUIREMENT = "new_import_requirement"
    NEW_TOP_LEVEL_PACKAGE = "new_top_level_package"
    NONLOCAL_SAFETY_SKIP = "nonlocal_safety_skip"
    NOT_STRUCTURALLY_SIMILAR = "not_structurally_similar"
    ORPHANED_VARIABLES = "orphaned_variables"
    PRIVATE_NAME_LEXICAL_CLASS = "private_name_lexical_class"
    REBOUND_EXTERNAL_BINDING = "rebound_external_binding"
    RELATIVE_IMPORT_ACROSS_PACKAGES = "relative_import_across_packages"
    RETURN_VARIABLES_NOT_ALIGNED = "return_variables_not_aligned"
    RUN_BY_PATH_IMPORT = "run_by_path_import"
    SUPER_IN_CALL = "super_in_call"
    TRIVIAL_FORWARDING_HELPER = "trivial_forwarding_helper"
    TRIVIAL_RETURN_BLOCKS = "trivial_return_blocks"
    UNBINDS_EXTERNAL_NAME = "unbinds_external_name"
    DUPLICATE_PROPOSAL = "duplicate_proposal"
    FORWARDED_CALLEE = "forwarded_callee"
    THUNK_OF_POSSIBLY_UNBOUND_LOCAL = "thunk_of_possibly_unbound_local"
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
    # Names the template block's call site can resolve (see ``available_argument_names``).
    available_names: FrozenSet[str]
    # What the helper returns, in the template block's spelling, and every
    # name that block binds: a clustered occurrence may read after its block
    # only names that map into the former, and its own block must bind them.
    return_variables: Tuple[str, ...]
    bound_in_block: FrozenSet[str]
    # Shared free names the helper reads as bare module references; an
    # occurrence may join only where every read of them resolves the same way.
    module_names: FrozenSet[str]


def span_contains(node: ast.stmt, line_range: Tuple[int, int]) -> bool:
    """Whether the source span of ``node`` covers every line of ``line_range``."""
    start, end = line_range
    return node.lineno <= start and end <= (node.end_lineno or node.lineno)


def encloses(outer: FunctionNode, inner: FunctionNode) -> bool:
    """Whether ``inner`` is ``outer`` or lies within its source span."""
    return outer is inner or span_contains(outer, (inner.lineno, inner.end_lineno or inner.lineno))


def proposal_identity(proposal: RefactoringProposal) -> Hashable:
    """What makes two proposals the same refactoring: the helper, its home, and its sites.

    Many pairs of a file of similar functions propose one helper over one
    set of clustered sites; the pair that found it first names it, the rest
    add nothing. The helper's generated name is left out, since each
    proposal mints its own. A site is its span: the same helper over the
    same block yields the same call, since the arguments are the block's own
    expressions in the helper's parameter order.
    """
    return (
        proposal.file_path,
        proposal.insert_into_class,
        proposal.insert_into_function,
        proposal.method_kind,
        proposal.method_param_name,
        GENERATED_HELPER_NAME.sub("", ast.dump(proposal.extracted_function)),
        tuple(
            sorted(
                (replacement.file_path or proposal.file_path, replacement.line_range)
                for replacement in proposal.replacements
            )
        ),
    )
