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

"""The state and cross-cutting operations the engine's mixins rely on.

``UnificationRefactorEngine`` is assembled from mixins, one per
responsibility (block analysis, pair evaluation, placement, reuse,
insertion, annotation wiring, materialization, clustering, parallel
evaluation, the fixed-point drivers). Each mixin is a class in its own
module and reaches the rest of the engine only through the attributes and
methods declared here, so a reader of one module sees exactly what it
depends on, and mypy checks that the engine provides it. The engine's
constructor assigns every attribute; each method stub names the class that
implements it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import (
    NamedTuple,
    Any,
    Callable,
    Hashable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from weakref import WeakKeyDictionary

from ..diagnostics import Settings
from ..type_inference import CheckResult, TypeOracle
from .block_signature import BlockSignature
from .bounded_cache import BoundedCache
from .extractor import HygienicExtractor
from .function_index import FunctionIndex
from .models import (
    AppliedChange,
    ClusterContext,
    CodeBlockPair,
    FunctionArtifact,
    FunctionNode,
    RefactoringProposal,
    RejectReason,
    Replacement,
)
from .structural_memo import StoredSubstitution
from .unifier import Unifier
from .progress import DEFAULT_PROGRESS, ProgressMode
from .import_graph import ImportGraphCache


class GuardKey(NamedTuple):
    """What a block guard's verdict depends on."""

    guard: Callable[..., bool]
    function_id: Optional[str]
    block_id: str
    module_digest: Optional[str]


class TemplateKey(NamedTuple):
    """Everything a helper template carries that a clustered call site depends on."""

    block_id: str
    free_vars: FrozenSet[str]
    enclosing_names: FrozenSet[str]
    is_value_producing: bool
    globals_to_declare: Tuple[str, ...]
    nonlocals_to_declare: Tuple[str, ...]
    helper_name: str
    helper_dump: str
    param_order: Tuple[Tuple[str, int], ...]
    preamble_length: int
    available_names: FrozenSet[str]
    return_variables: Tuple[str, ...]
    module_names: FrozenSet[str]
    bound_in_block: FrozenSet[str]


class ClusterScanKey(NamedTuple):
    """What the scan of a file for clustered sites depends on.

    The file's content, where the helper will be visible from (the position
    and structure of the function it is inserted into, or None for the
    module), the template, and the size gate.
    """

    template: TemplateKey
    file_path: str
    module_digest: str
    # The helper's enclosing function (line, column, structural id), or None at module level.
    helper_home: Optional[Tuple[int, int, str]]
    min_lines: int


@dataclass(frozen=True)
class ClusteredSite:
    """A block that can call a template's helper, with the method context of its function.

    The scan's sites are shared by every pair that produces the template;
    the replacement and its call node are never mutated.
    """

    replacement: Replacement
    context: ClusterContext


class EngineState:
    """Attributes and operations shared across the engine's mixins (declarations only)."""

    _source_lines_cache: Dict[str, Tuple[Tuple[int, int, int], Tuple[str, ...]]]
    """Lines of files the apply path read, keyed by path, with the stat they were read at."""

    import_graph: ImportGraphCache
    """What this run has learned about the project's import graph."""

    _helper_name_counters: Dict[str, int]
    # Identities of the proposals this analysis has finished; a pair whose
    # proposal repeats one is declined before reuse, filtering and annotation.
    _seen_proposals: Set[Hashable]
    """Next helper number per file, so generated names are unique across a run."""

    incremental_global_passes: bool
    """Whether later global passes re-pair only the files rewritten since the last one."""

    _settings: Settings
    """What Towel read from the environment at construction."""

    min_lines: int
    """Minimum source lines a duplicated block must span."""

    max_candidate_pairs: int
    """Candidate pairs an analysis evaluates before it leaves the largest buckets out."""

    extractor: HygienicExtractor
    """Renders helpers and call sites."""

    """Memo of the per-candidate clustering pipeline; a hit is the same node, never mutated."""

    _cluster_scan_cache: BoundedCache["ClusterScanKey", Tuple[ClusteredSite, ...]]
    """Memo of the whole scan of a file for a template's clustered sites, before overlap filtering."""

    skip_trivial_helpers: bool
    """Whether a helper that only forwards, renames, or unpacks is declined."""

    reuse_existing_functions: bool
    """Whether a whole-body duplicate calls the function it already is."""

    annotate_helpers: bool
    """Whether helpers carry the annotations their call sites declare."""

    _change_log: List[AppliedChange]
    """Every call site rewritten so far in the current run."""

    snippet_formatter: Optional[Callable[[str], str]]
    """Formats each inserted snippet, or None to insert the rendering as is."""

    file_finisher: Optional[Callable[[str, str], str]]
    """Finishes each modified file (imports sorted), or None."""

    prefer_absolute_imports: Optional[bool]
    """Cross-file helper import style; None lets the discovered layout decide."""

    pep420_namespace_packages: Optional[bool]
    """Whether directories without __init__.py are packages; None infers it."""

    @staticmethod
    def _block_line_span(block: Sequence[ast.stmt]) -> Optional[Tuple[int, int]]:
        """The (start_line, end_line) of a contiguous block; provided by InsertionPoints."""
        raise NotImplementedError

    # The project's type checker, when one is installed and wanted.
    type_oracle: Optional[TypeOracle]
    _type_run_oracle: Optional[TypeOracle]
    """Per-run view of the caller's oracle, relocated when the driver copies its input."""
    _type_run_baseline: Optional[CheckResult]
    """Original complete-project result; None means the run has not checked its baseline."""
    _analysis_paths: Tuple[str, ...]
    """Paths from the latest analysis, used to seed a direct application's initial check."""
    # The unifier every pair is matched with; its options are fixed at
    # construction.
    unifier: Unifier
    # Every function of the current analysis, keyed by its node, to the file
    # it was parsed from.
    _function_paths: WeakKeyDictionary[FunctionNode, str]
    # Memoization caches keyed by the identity of AST nodes parsed for this
    # engine run; the weak ones vanish with their trees.
    _assignment_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]]
    _used_names_cache: WeakKeyDictionary[ast.AST, FrozenSet[str]]
    _value_producing_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]]
    _signed_block_cache: WeakKeyDictionary[
        FunctionNode, List[Tuple[Tuple[int, int], List[ast.stmt], BlockSignature]]
    ]
    # The index of the current analysis's functions, keyed by the list it
    # was built from; see ``_function_index``.
    _function_index_cache: Optional[Tuple[Sequence[FunctionArtifact], FunctionIndex]]
    # Bounded, path-registered caches: guards per (guard, function, block),
    # unification results per block-structure pair, and the per-block analyses.
    _block_guard_cache: BoundedCache["GuardKey", bool]
    _unify_cache: BoundedCache[Tuple[str, str], Optional[StoredSubstitution]]
    # Per-block analyses of several result types; ``_per_block`` narrows each.
    _per_block_cache: BoundedCache[Tuple[str, str, str], object]
    # The last few parsed sources of the apply path, which parses each modified
    # file several times per proposal.
    _parse_cache: BoundedCache[str, ast.Module]

    def _get_indent(self, line: str) -> str:
        """The indentation of a line; provided by InsertionPoints."""
        raise NotImplementedError

    @classmethod
    def placeable_after(cls, source: str) -> Set[str]:
        """Module-level definitions a helper may follow; provided by InsertionPoints."""
        raise NotImplementedError

    def _allocate_helper_name(
        self,
        file_path: str,
        *,
        class_context: bool = False,
        related_paths: Sequence[str] = (),
    ) -> str:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _find_python_files(self, directory: str, recursive: bool = True) -> List[str]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def analyze_directory(
        self,
        directory: str,
        recursive: bool = True,
        *,
        progress: ProgressMode = DEFAULT_PROGRESS,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def analyze_files(
        self,
        file_paths: List[str],
        *,
        progress: ProgressMode = DEFAULT_PROGRESS,
        invalidate_paths: Optional[List[str]] = None,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def invalidate_paths(self, paths: List[str]) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _module_digest(self, func: Optional[FunctionNode]) -> Optional[str]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _sid(self, nodes: Sequence[ast.AST]) -> str:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _debug_reject(
        self, reason: RejectReason, pair: "CodeBlockPair", detail: Optional[str] = None
    ) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _parse_source(self, source: str) -> ast.Module:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _remember(
        self, paths: Iterable[Optional[str]], cache: MutableMapping[Any, Any], key: Any
    ) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _function_index(self, all_functions: Sequence[FunctionArtifact]) -> FunctionIndex:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError
