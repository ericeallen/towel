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
from pathlib import Path
from typing import (
    NamedTuple,
    Any,
    Callable,
    Hashable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from weakref import WeakKeyDictionary

from ..coverage_config import CoverageExclusion, coverage_exclusion
from ..diagnostics import LOG, Settings
from ..project_layout import find_project_root
from ..type_baseline import CheckedChange, KnownErrors
from ..type_inference import CheckResult, TypeDiagnostic, TypeOracle
from .block_signature import BlockSignature
from .bounded_cache import BoundedCache
from .extractor import HygienicExtractor
from .function_index import FunctionIndex
from .models import (
    AppliedChange,
    ClassInfo,
    ClusterContext,
    CodeBlockPair,
    FunctionArtifact,
    FunctionNode,
    RefactoringProposal,
    RejectReason,
    Replacement,
)
from .static_positions import TypingForms
from .structural_memo import StoredSubstitution
from .unifier import Unifier
from .progress import DEFAULT_PROGRESS, ProgressMode
from .import_graph import ImportGraphCache
from .namespace_writes import ProjectWrites


class BlockSite(NamedTuple):
    """One block of one function of one module, told apart from every other block.

    The guards and per-block analyses read more than the block's own code:
    the function's other statements, where in them the block stands (what
    is bound before and after it, whether it is nested in a loop), the
    scopes enclosing the function, and the module's hazards (``global`` and
    ``nonlocal`` rebinding, reflection) and aliases. The module's source
    fixes its tree, and so all of these; the positions pick out the function
    and the block's statements in that tree. Two blocks of the same code in
    other places are other sites, however alike their structure.
    """

    # SHA-256 of the module's source.
    module_digest: str
    # The function's line and column.
    function_position: Tuple[int, int]
    # The first statement's line and column, and the number of statements.
    block_position: Tuple[int, int, int]


class GuardKey(NamedTuple):
    """What a block guard's verdict depends on: the guard and where the block stands."""

    guard: Callable[..., bool]
    site: BlockSite


class PerBlockKey(NamedTuple):
    """What a per-block analysis depends on: which analysis, and where the block stands."""

    analysis: str
    site: BlockSite


class UnifyKey(NamedTuple):
    """What unifying two blocks depends on: their structure, and what their callees denote.

    Two blocks of one structure are unified alike unless a callee of one is a
    typing form where the other's is not, as ``cast`` from typing is and
    sqlglot's ``exp.cast`` is not.
    """

    block1_id: str
    block2_id: str
    forms1: TypingForms
    forms2: TypingForms


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
class HelperNameClaims:
    """The helper-shaped names a project's sources already define, by what they could displace.

    Each is the name as Python stores it, so ``def __extracted_func_0`` in
    ``class A`` is ``_A__extracted_func_0``. ``namespace`` holds what an
    attribute store, ``setattr`` or a namespace subscript writes, any of
    which could replace a module-level helper; ``members`` holds those and
    every class member, and a ``type(...)`` namespace's keys, which could
    override or shadow a method helper stored under the same name.
    """

    namespace: FrozenSet[str] = frozenset()
    members: FrozenSet[str] = frozenset()


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
    """Next helper number per file, so generated names are unique across a run."""
    _project_helper_names: Dict[str, HelperNameClaims]
    """The helper-shaped names each project root's sources already define, by root."""
    _namespace_writes: Dict[str, ProjectWrites]
    """The writes into module namespaces each project root's sources make, by root."""
    _coverage_exclusions: Dict[str, CoverageExclusion]
    """What each project root's coverage.py excludes lines by, read once per engine."""
    # Identities of the proposals this analysis has finished; a pair whose
    # proposal repeats one is declined before reuse, filtering and annotation.
    _seen_proposals: Set[Hashable]
    _pair_rejection: Optional[RejectReason]
    """The reason the pair being decided was last declined for, or None."""
    _pair_rejections: Dict[str, int]
    """How many candidate pairs the latest analysis declined, by reason."""
    _checker_refusals: int
    """Rendered variants the checker refused since the driver last started a proposal."""

    incremental_global_passes: bool
    """Whether later global passes re-pair only the files rewritten since the last one."""

    _settings: Settings
    """What Towel read from the environment at construction."""

    min_lines: int
    """Minimum source lines a duplicated block must span."""

    parameterize_builtins: bool
    """Whether a builtin that may differ between sites is passed as a parameter instead of declined."""

    max_candidate_pairs: int
    """Candidate pairs an analysis evaluates before it leaves the largest buckets out."""

    extractor: HygienicExtractor
    """Renders helpers and call sites."""

    """Memo of the per-candidate clustering pipeline; a hit is the same node, never mutated."""

    _cluster_scan_cache: BoundedCache["ClusterScanKey", Tuple[ClusteredSite, ...]]
    """Memo of the whole scan of a file for a template's clustered sites, before overlap filtering."""

    skip_trivial_helpers: bool
    """Whether a helper that only forwards, renames, or unpacks is declined."""

    cross_module_helpers: bool
    """Whether a helper may be shared across modules, and so an import of one written."""

    annotate_helpers: bool
    """Whether helpers carry the annotations their call sites declare."""

    _change_log: List[AppliedChange]
    """Every call site rewritten so far in the current run."""

    snippet_formatter: Optional[Callable[[str], str]]
    """Formats each inserted snippet, or None to insert the rendering as is."""

    file_finisher: Optional[Callable[[str, str], str]]
    """Finishes each modified file (imports sorted), or None."""

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
    _type_known: KnownErrors
    """What the project's check reports as it now stands, the original's errors to begin with."""
    _type_checked: Optional[CheckedChange]
    """The change the checker last accepted, until the driver writes it or checks another."""
    _type_names_any: Mapping[str, Tuple[TypeDiagnostic, ...]]
    """The files the original check names what it cannot type in, which no change may touch."""
    _type_unlooked: Mapping[str, Tuple[str, Tuple[Tuple[int, int], ...]]]
    """Per file, the digest of its original text and the regions the checker did not look at."""
    _analysis_paths: Tuple[str, ...]
    """Paths from the latest analysis, used to seed a direct application's initial check."""
    _output_origin: Optional[Tuple[Path, Path]]
    """(input, output) when the driver copied its input. A checker answers in
    the input project's module names, since that is what it checks the copy
    under, so such a name resolves against the input's layout, not the copy's."""
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
    # Bounded caches: guards per (guard, block site), unification results per
    # block-structure pair, and the per-block analyses per block site.
    _block_guard_cache: BoundedCache["GuardKey", bool]
    _unify_cache: BoundedCache[UnifyKey, Optional[StoredSubstitution]]
    # Per-block analyses of several result types; ``_per_block`` narrows each.
    _per_block_cache: BoundedCache[PerBlockKey, object]
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
        prefix: str,
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

    def _origin_of(self, path: str) -> str:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _coverage_exclusion(self, file_path: str) -> CoverageExclusion:
        """What the coverage.py of the project around ``file_path`` excludes lines by.

        Read from the project's own location, since an output directory is
        only a copy of part of it, once per engine and root. A configuration
        coverage.py could not read gives its defaults, and the run says so.
        """
        root = find_project_root(Path(self._origin_of(file_path)))
        key = str(root)
        found = self._coverage_exclusions.get(key)
        if found is None:
            found = self._coverage_exclusions[key] = coverage_exclusion(root)
            if found.problem is not None:
                LOG.warning(
                    "warning: coverage.py could not read its configuration in %s (%s); moved"
                    " code is judged against coverage.py's default exclusions",
                    root,
                    found.problem,
                )
        return found

    def _module_digest(self, func: Optional[FunctionNode]) -> Optional[str]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _sid(self, nodes: Sequence[ast.AST]) -> str:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _block_site(self, func: FunctionNode, nodes: Sequence[ast.stmt]) -> Optional[BlockSite]:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _debug_reject(
        self, reason: RejectReason, pair: "CodeBlockPair", detail: Optional[str] = None
    ) -> None:
        """Provided by UnificationRefactorEngine."""
        raise NotImplementedError

    def _judge_pair(
        self,
        pair: "CodeBlockPair",
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
    ) -> Optional[RefactoringProposal]:
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
