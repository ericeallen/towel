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

"""The engine's core: construction, caches, analysis entry points, block enumeration, pairing.

``UnificationRefactorEngine`` is assembled from the mixins under
``unification/`` (block analysis, the pair decision, placement, reuse,
insertion, annotation wiring, materialization, clustering, parallel
evaluation, the fixed-point drivers) over ``EngineState``. This module
keeps what every mixin builds on: the constructor and its caches, the
entry points that analyze a file, a set of files or a directory, the
enumeration of candidate blocks in every function and method (nested ones
included), and the pairing of blocks that share a signature bucket -- within
one module unless ``cross_module_helpers`` is set -- leaving the largest
buckets out when the projected pair count exceeds ``max_candidate_pairs``.
"""

import ast
import sys
from dataclasses import dataclass
import os
import re
from typing import (
    Set,
    Any,
    Callable,
    Hashable,
    List,
    Tuple,
    Dict,
    Optional,
    FrozenSet,
    Iterable,
    Mapping,
    MutableMapping,
    Sequence,
)
from weakref import WeakKeyDictionary
from pathlib import Path
from .unifier import Unifier
from .extractor import (
    HygienicExtractor,
)
from .structural_memo import (
    StoredSubstitution,
    structural_id,
)
from .progress import (
    ProgressBar,
    DEFAULT_PROGRESS,
    ProgressMode,
    load_tqdm,
    quietly,
    wants_bar,
    render_inline_bar,
)
from .parallel import ParallelEvaluation
from .bounded_cache import BoundedCache
from .engine_state import (
    BlockSite,
    ClusteredSite,
    ClusterScanKey,
    GuardKey,
    HelperNameClaims,
    PerBlockKey,
    UnifyKey,
)
from .defaults import DEFAULT_MAX_CANDIDATE_PAIRS, DEFAULT_MAX_PARAMETERS, DEFAULT_MIN_LINES
from .function_index import FunctionIndex
from ..diagnostics import LOG, REJECTIONS, Settings, debugging
from .import_graph import ImportGraphCache
from .namespace_writes import ProjectWrites
from .block_signature import (
    BlockBucketKey,
    BlockSignature,
    quick_filter,
    signature_bucket_key,
)
from ..coverage_config import CoverageExclusion
from .models import (
    CodeBlockPair,
    FunctionArtifact,
    ClassInfo,
    PairVerdict,
    RefactoringProposal,
    RejectReason,
    AppliedChange,
    FunctionNode,
    identity_digest,
)
from ..type_baseline import KnownErrors
from ..type_inference import TypeOracle
from ..source_files import python_sources
from ..source_text import read_source
from .pipeline import run_pipeline, AnalysisSession

_SignedBlock = Tuple[Tuple[int, int], List[ast.stmt], BlockSignature]


@dataclass(frozen=True)
class _FunctionBuckets:
    """One function's blocks grouped by bucket key, in their original order."""

    block_keys: List[BlockBucketKey]
    by_key: Dict[BlockBucketKey, List[_SignedBlock]]

    @property
    def keys(self) -> FrozenSet[BlockBucketKey]:
        return frozenset(self.by_key)

    def without(self, keys: FrozenSet[BlockBucketKey]) -> "_FunctionBuckets":
        """These buckets less the given keys; the per-block key list keeps its positions."""
        return _FunctionBuckets(
            self.block_keys,
            {key: members for key, members in self.by_key.items() if key not in keys},
        )


def _bucketed(blocks: Sequence[_SignedBlock]) -> _FunctionBuckets:
    """Group a function's signed blocks by bucket key; each bucket keeps block order."""
    block_keys = [signature_bucket_key(signature) for _, _, signature in blocks]
    by_key: Dict[BlockBucketKey, List[_SignedBlock]] = {}
    for key, block in zip(block_keys, blocks):
        by_key.setdefault(key, []).append(block)
    return _FunctionBuckets(block_keys, by_key)


@dataclass(frozen=True)
class _Partners:
    """Which later functions each function of an analysis is paired with.

    Every later function when helpers may be shared across modules, and
    otherwise only the later functions of its own module, found through a
    list per module so that pairs never formed cost nothing to skip.
    """

    count: int
    by_module: Optional[Mapping[int, Tuple[Sequence[int], int]]]
    """Per function, its module's function indices and its own place among them; None: no limit."""

    @classmethod
    def of(cls, functions: Sequence[FunctionArtifact], across_modules: bool) -> "_Partners":
        if across_modules:
            return cls(len(functions), None)
        members: Dict[str, List[int]] = {}
        for index, entry in enumerate(functions):
            members.setdefault(entry.file_path, []).append(index)
        return cls(
            len(functions),
            {
                index: (indices, place)
                for indices in members.values()
                for place, index in enumerate(indices)
            },
        )

    def after(self, index: int) -> Sequence[int]:
        """The functions after ``index``, in order, that it is paired with."""
        if self.by_module is None:
            return range(index + 1, self.count)
        indices, place = self.by_module[index]
        return indices[place + 1 :]

    @property
    def function_pairs(self) -> int:
        if self.by_module is None:
            return self.count * (self.count - 1) // 2
        return sum(len(indices) - place - 1 for indices, place in self.by_module.values())

    def group(self, index: int) -> int:
        """Functions that may pair share a group: every one, or the first of its module."""
        return 0 if self.by_module is None else self.by_module[index][0][0]


def _buckets_over_budget(
    buckets: Sequence[_FunctionBuckets], budget: int, partners: _Partners
) -> FrozenSet[BlockBucketKey]:
    """The bucket keys to leave out so the projected pair count fits the budget.

    A bucket key groups blocks with one statement-type sequence, and only
    blocks in one bucket pair, so the pairs the loop will form are, per
    key, the pairs among the blocks of functions that may pair (``partners``:
    all of them, or those of one module) less the pairs within one function.
    Keys are dropped largest first until the rest fit; each dropped key is
    named in a warning, since its blocks will not be proposed.
    """
    if budget <= 0:
        return frozenset()
    across: Dict[BlockBucketKey, int] = {}
    grouped: Dict[Tuple[int, BlockBucketKey], int] = {}
    within: Dict[BlockBucketKey, int] = {}
    for index, bucket in enumerate(buckets):
        group = partners.group(index)
        for key, members in bucket.by_key.items():
            count = len(members)
            across[key] = across.get(key, 0) + count
            grouped[(group, key)] = grouped.get((group, key), 0) + count
            within[key] = within.get(key, 0) + count * (count - 1) // 2
    projected = {key: -within[key] for key in across}
    for (_, key), total in grouped.items():
        projected[key] += total * (total - 1) // 2
    remaining = sum(projected.values())
    if remaining <= budget:
        return frozenset()
    dropped: Set[BlockBucketKey] = set()
    for key, pairs in sorted(projected.items(), key=lambda item: item[1], reverse=True):
        if remaining <= budget:
            break
        dropped.add(key)
        remaining -= pairs
    LOG.warning(
        "%d candidate pairs exceed the budget of %d; leaving out %d bucket(s) holding %d "
        "similar blocks (raise --max-pairs or --min-lines to change this)",
        sum(projected.values()),
        budget,
        len(dropped),
        sum(across[key] for key in dropped),
    )
    return frozenset(dropped)


class _PairingProgress:
    """Progress of the pairing loop: a tqdm bar when wanted and available, else an inline bar."""

    def __init__(self, progress: ProgressMode, total_function_pairs: int) -> None:
        self._total = total_function_pairs
        self._done = 0
        self._last_pct = -1
        self._bar: Optional[ProgressBar] = None
        wants = wants_bar(progress) and total_function_pairs > 0
        if wants:
            factory = load_tqdm()
            if factory is not None:
                self._bar = factory(
                    total=total_function_pairs,
                    desc="pairs",
                    unit="fp",
                    dynamic_ncols=True,
                    leave=False,
                )
        self._inline = wants and self._bar is None
        if self._inline:
            print("Pairing blocks:", end=" ", flush=True, file=sys.stderr)

    def function_pair_done(self, pairs_so_far: int) -> None:
        """One more function pair has been examined; ``pairs_so_far`` block pairs exist."""
        self._done += 1
        bar = self._bar
        if bar is not None:
            done = self._done

            def advance() -> None:
                bar.update(1)
                if done % 20 == 0 or done == self._total:
                    bar.set_postfix({"pairs": pairs_so_far}, refresh=True)

            quietly(advance)
        elif self._inline:
            pct = int(100 * self._done / max(self._total, 1))
            if pct != self._last_pct:
                self._last_pct = pct
                print(
                    f"\rPairing blocks: [{render_inline_bar(pct, bar_len=24)}] {pct:3d}%"
                    f" | pairs={pairs_so_far}",
                    end="",
                    flush=True,
                    file=sys.stderr,
                )

    def finish(self) -> None:
        if self._bar is not None:
            quietly(self._bar.close)
        elif self._inline:
            print(file=sys.stderr)


def _traced_block(file_path: str, function: str, block_range: Optional[Tuple[int, int]]) -> str:
    """A block as the rejection trace names it: ``path::function@(start, end)``."""
    return f"{file_path}::{function}" + (f"@{block_range}" if block_range else "")


def _size_or_zero(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class UnificationRefactorEngine(ParallelEvaluation):
    """The engine: find duplicated blocks, verify an extraction for each, apply to a fixed point.

    Assembled from the mixins above, one per responsibility, over an
    ``EngineState`` that declares what they share; docs/ARCHITECTURE.md maps
    each stage to its module.
    """

    def __init__(
        self,
        max_parameters: int = DEFAULT_MAX_PARAMETERS,
        min_lines: int = DEFAULT_MIN_LINES,
        parameterize_constants: bool = True,
        *,
        prefer_absolute_imports: Optional[bool] = None,
        pep420_namespace_packages: Optional[bool] = None,
        promote_equal_hof_literals: bool = False,
        parameterize_builtins: bool = False,
        excluded_directories: Sequence[str] = (),
        cross_module_helpers: bool = False,
        skip_trivial_helpers: bool = True,
        reuse_existing_functions: bool = True,
        annotate_helpers: bool = True,
        type_oracle: Optional[TypeOracle] = None,
        snippet_formatter: Optional[Callable[[str], str]] = None,
        file_finisher: Optional[Callable[[str, str], str]] = None,
        incremental_global_passes: bool = True,
        max_candidate_pairs: int = DEFAULT_MAX_CANDIDATE_PAIRS,
        settings: Optional[Settings] = None,
    ):
        """
        Initialize the refactoring engine.

        Args:
            max_parameters: Maximum parameters an extracted helper may take; a
                candidate needing more is rejected (default: 5).
            min_lines: Minimum number of source lines a duplicated block must span
                to be considered (default: 3).
            max_candidate_pairs: Most block pairs one analysis evaluates. Blocks
                are quadratic in a function's length and pairs quadratic in
                blocks, so a file of many long, similar functions can propose
                tens of millions of pairs and exhaust memory; past the budget the
                largest buckets of similar blocks are left out, with a warning
                (default: 20,000,000).
            parameterize_constants: Whether differing constants across the matched
                blocks become helper parameters (default: True).
            prefer_absolute_imports: Deprecated, and has no effect. It chose an
                absolute or a relative import for a cross-file helper, a name
                read from packaging metadata; since 1.772 every import Towel
                writes is spelled as the program's own imports show it works
                (docs/DECISIONS.md, "Import names come from the program"), so
                there is nothing left to choose. Still accepted so existing
                callers keep working; it will be removed in a later release.
            pep420_namespace_packages: Deprecated, and has no effect, for the
                same reason: whether a directory without ``__init__.py`` is a
                package is read from the imports that use it.
            promote_equal_hof_literals: Expose literal arguments of higher-order
                factory calls as parameters even when they are equal across blocks
                (Option B policy); default False.
            parameterize_builtins: Where a builtin the duplicated code reads may
                differ between its sites -- one site's function binds ``len`` and
                the other reads the builtin, or, across modules, the program shows
                a participating module may hold the name -- pass it to the helper
                as a parameter, each site giving its own, instead of declining
                the pair (default: False). A builtin every site reads alike is
                still read bare.
            excluded_directories: Directory names to skip in directory mode, such
                as a package that carries its own test suite. The program's
                import model reads nothing in them either, which is how a stray
                copy of a package that makes its name ambiguous is set aside;
                what an import that enters one runs is then unknown, and no
                host whose import would enter one is taken.
            cross_module_helpers: Share a helper between duplicates in different
                modules, importing it from the one that hosts it into the others
                (default: False). Off, only duplicates within one module are
                paired, and no import of a project module is ever written, not
                even one a helper's annotations would read under
                ``TYPE_CHECKING``: an extraction then changes nothing about how
                the project's modules depend on each other.
            skip_trivial_helpers: Skip proposing a helper whose body is a single
                forwarding statement -- a lone ``raise``, a ``return`` of one
                call, or a bare call -- which adds indirection without sharing any
                logic (default: True).
            reuse_existing_functions: Deprecated, and has no effect. It chose
                whether a site that is the whole body of a plain module-level
                function was left alone and the other sites rewritten to call
                it; since 1.772 every whole-body site calls a new helper
                instead, because such a call looked the other function up in
                its module each time, so patching or rebinding it changed both.
                Still accepted so existing callers keep working; it will be
                removed in a later release.
            annotate_helpers: Give a helper the parameter and return annotations
                its call sites agree on -- an annotated, never-rebound parameter
                of the enclosing function, a literal's builtin type, the sites'
                declared return type -- in code that already uses annotations
                (default: True). Nothing is inferred unless ``type_oracle``
                is given.
            type_oracle: Checks the complete original project before the run's
                first application, then infers where sites declare types and
                verifies every prospective change. Errors the original check
                reports are left as they are, and a change is rejected only for
                an error they do not account for (``towel.type_baseline``); a
                checker that cannot run refuses application with a distinct
                diagnostic. Direct applications share an implicit run until
                ``begin_refactoring_run(paths)`` starts another; each fixed-point
                call starts its own run. None (default) infers and checks nothing.
                The caller retains ownership of the oracle and must close it.
            snippet_formatter: Renders each generated helper definition and
                call statement from ``ast.unparse`` output to the text that is
                inserted, for example Black (see ``towel.formatting``). None
                (default) inserts the ``ast.unparse`` text as is.
            file_finisher: Maps ``(path, source)`` of each modified file to its
                final text, for example with imports sorted the way the project
                sorts them (see ``towel.formatting.import_sorter_for_project``).
                None (default) leaves files as assembled.
            incremental_global_passes: In directory mode, after the first
                analysis, re-pair only functions in files that changed since
                the previous global pass (default: True). This is exact: an
                unchanged pair's verdict depends on its two files, the class
                hierarchy (which refactoring never alters) and the import
                graph (to which refactoring only adds edges, so a pair
                declined for a cycle stays declined), and any proposal it
                produced was applied, which changed its files. False re-pairs
                everything on every global pass.
            settings: What Towel reads from the environment (worker cap,
                debug switches). Read once from ``os.environ`` when omitted.
        """
        self._settings = settings if settings is not None else Settings.from_environ()
        self.analysis_session = AnalysisSession(
            check_ast_immutable=self._settings.check_ast_immutable
        )
        # Names come from the program's imports, less the directories the
        # run leaves out.
        self.import_graph = ImportGraphCache(excluded_names=excluded_directories)
        self._source_lines_cache: Dict[str, Tuple[Tuple[int, int, int], Tuple[str, ...]]] = {}
        self.max_parameters = max_parameters
        self.min_lines = min_lines
        self.max_candidate_pairs = max_candidate_pairs
        self.skip_trivial_helpers = skip_trivial_helpers
        # reuse_existing_functions is accepted and ignored; see the docstring.
        self.annotate_helpers = annotate_helpers
        self.type_oracle = type_oracle
        self._type_run_oracle = type_oracle
        self._type_run_baseline = None
        self._type_known = KnownErrors()
        self._type_checked = None
        self._type_names_any = {}
        self._type_unlooked = {}
        self._type_unreadable = frozenset()
        self.snippet_formatter = snippet_formatter
        self.file_finisher = file_finisher
        self.incremental_global_passes = incremental_global_passes
        # Directory names left out of directory mode, such as ``tests`` when a
        # package carries its test suite inside itself (networkx: 77k of its
        # 198k lines).
        self.excluded_directories = tuple(excluded_directories)
        self.cross_module_helpers = cross_module_helpers
        self.parameterize_constants = parameterize_constants
        self.parameterize_builtins = parameterize_builtins
        self.unifier = Unifier(
            max_parameters=max_parameters,
            parameterize_constants=parameterize_constants,
            promote_equal_hof_literals=promote_equal_hof_literals,
        )
        self.extractor = HygienicExtractor()
        # prefer_absolute_imports and pep420_namespace_packages are accepted
        # and ignored; see the docstring.
        # Default behavior: allow safe handling of globals/nonlocals by not parameterizing
        # them and promoting necessary declarations into the extracted function when needed.

        # Memoization caches keyed by the identity of AST nodes parsed for this engine run.
        self._assignment_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]] = WeakKeyDictionary()
        self._used_names_cache: WeakKeyDictionary[ast.AST, FrozenSet[str]] = WeakKeyDictionary()
        self._value_producing_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]] = (
            WeakKeyDictionary()
        )
        # A block guard is pure in the guard and the block's site (``BlockSite``:
        # its module's source and its function's and statements' positions); a
        # block takes part in every pair it forms, so its verdicts are computed
        # once. Keyed by value, so bounded and self-validating: a rewritten
        # module has another digest, and never evicted by path.
        self._block_guard_cache: BoundedCache[GuardKey, bool] = BoundedCache(
            self.STRUCTURAL_CACHE_LIMIT
        )
        # Unification is a function of the two blocks' nodes. The clustering
        # pass unifies one template against the same candidates for every pair
        # that shares it, so results are memoized per (block, block) within an
        # analysis; callers mutate substitutions, so a hit returns a copy.
        # Keyed by the two blocks' structure and what their callees denote
        # among the typing forms, valued by positions: a hit serves any later
        # block with the same structure and forms, including the same code
        # after a re-parse. Bounded and self-validating, so never evicted by
        # path.
        self._unify_cache: BoundedCache[UnifyKey, Optional[StoredSubstitution]] = BoundedCache(
            self.STRUCTURAL_CACHE_LIMIT
        )
        # Every pair that yields one template scans the whole file for sites
        # that can share its helper; with N similar blocks that is N^2 pairs
        # each scanning N sites. The scan is a function of the file's content
        # and the template alone, so it is computed once per template and its
        # sites are shared (the pair's own blocks are filtered out on the way
        # out). Keyed by content digest, so never evicted by path.
        self._cluster_scan_cache: BoundedCache[ClusterScanKey, Tuple[ClusteredSite, ...]] = (
            BoundedCache(
                self.CLUSTER_SCAN_CACHE_LIMIT,
                weight=len,
                weight_limit=self.CLUSTER_SCAN_SITE_LIMIT,
            )
        )
        # Structural ids per block, keyed weakly by the block's first node and
        # then by its length, so an entry vanishes with its tree instead of
        # pinning it (block entries were never evicted before).
        self._structural_ids: WeakKeyDictionary[ast.AST, Dict[int, str]] = WeakKeyDictionary()
        # Which file each analyzed function came from, and a digest of that
        # file's source: weak, so a function whose tree the analysis session
        # has dropped is forgotten with it instead of pinning the tree.
        self._function_sources: WeakKeyDictionary[FunctionNode, str] = WeakKeyDictionary()
        # Per-block analyses (binding snapshot, reassignment and unbinding
        # checks, the external names another function may rebind) depend on
        # the block's site, like the guards above, and are keyed the same way.
        self._per_block_cache: BoundedCache[PerBlockKey, object] = BoundedCache(
            self.STRUCTURAL_CACHE_LIMIT
        )
        self._parse_cache: BoundedCache[str, ast.Module] = BoundedCache(64)
        # Every cache entry is registered under the absolute path(s) of the
        # file(s) it describes, so a file that changes between fixed-point
        # iterations evicts exactly its own entries and unchanged files keep
        # theirs across iterations.
        self._cache_entries_by_path: Dict[str, List[Tuple[MutableMapping[Any, Any], Any]]] = {}
        self._function_paths: WeakKeyDictionary[FunctionNode, str] = WeakKeyDictionary()
        self._function_index_cache: Optional[Tuple[Sequence[FunctionArtifact], FunctionIndex]] = (
            None
        )
        # Track helper name allocation per canonical file so helpers remain unique.
        self._helper_name_counters: Dict[str, int] = {}
        # Helper-shaped names anywhere in each project, scanned once per engine.
        self._project_helper_names: Dict[str, HelperNameClaims] = {}
        # Writes into module namespaces anywhere in each project, scanned once
        # per engine: patches of a builtin that decline a cross-module pair.
        self._namespace_writes: Dict[str, ProjectWrites] = {}
        # What each project's coverage.py excludes, read once per engine.
        self._coverage_exclusions: Dict[str, CoverageExclusion] = {}
        self._origins_in_run: Dict[Tuple[str, Optional[Tuple[Path, Path]]], Path] = {}
        self._seen_proposals: Set[Hashable] = set()
        self._pair_rejection: Optional[RejectReason] = None
        self._pair_rejection_subject: Optional[str] = None
        self._pair_rejections: Dict[str, int] = {}
        self._pair_identity: Optional[Hashable] = None
        self._trace_at_identity = 0
        self._captured_trace: Optional[List[str]] = None
        # The identities settled by this analysis's pairs so far, in pair order.
        self._settled_identities: Set[str] = set()
        self._checker_refusals = 0
        # Per-run record of what each applied extraction replaced: the original
        # block and the generated call, for the naming step's before/after view.
        self._change_log: List[AppliedChange] = []
        # Every file of the current analysis: helper names must be unique
        # across all of them, because any module may import from any other.
        self._analysis_paths: Tuple[str, ...] = ()
        self._output_origin: Optional[Tuple[Path, Path]] = None
        self._signed_block_cache: WeakKeyDictionary[
            FunctionNode, List[Tuple[Tuple[int, int], List[ast.stmt], BlockSignature]]
        ] = WeakKeyDictionary()

    # --- Debug helpers ---
    def _debug_reject(
        self,
        reason: RejectReason,
        pair: "CodeBlockPair",
        detail: Optional[str] = None,
        *,
        subject: Optional[str] = None,
    ) -> None:
        """Note why the pair is declined, and trace it when DEBUG_PROPOSAL_REJECTIONS is set.

        The reason is kept for ``_judge_pair``, which counts it, together with
        ``subject``, what the reason names, when it names something. The trace
        names each block by its file, function and line range
        (``path::function@(start, end)``), so a trace over many files locates
        every pair it declines.
        """
        self._pair_rejection = reason
        self._pair_rejection_subject = subject
        if not debugging(REJECTIONS):
            return
        first = _traced_block(pair.file_path, pair.function1_name, pair.block1_range)
        second = _traced_block(pair.file_path2, pair.function2_name, pair.block2_range)
        msg = f"REJECT[{reason}]: {first} <-> {second}"
        if detail:
            msg += f" :: {detail}"
        self._trace_rejection(msg)

    def _trace_rejection(self, line: str) -> None:
        """Write ``line`` to the rejection trace, or hold it for a verdict settled later."""
        if self._captured_trace is None:
            REJECTIONS.debug(line)
        else:
            self._captured_trace.append(line)

    def _debug_decline_site(
        self,
        reason: RejectReason,
        pair: "CodeBlockPair",
        function: FunctionNode,
        nodes: Sequence[ast.stmt],
        detail: object,
    ) -> None:
        """Trace a further occurrence that cannot join ``pair``'s helper, which the pair keeps.

        Not the pair's reason, since the pair is not declined: its helper is
        still proposed, for the sites that can share it.
        """
        if not debugging(REJECTIONS):
            return
        lines = (nodes[0].lineno, nodes[-1].end_lineno or nodes[-1].lineno) if nodes else None
        site = _traced_block(pair.file_path, function.name, lines)
        first = _traced_block(pair.file_path, pair.function1_name, pair.block1_range)
        second = _traced_block(pair.file_path2, pair.function2_name, pair.block2_range)
        self._trace_rejection(
            f"DECLINE-SITE[{reason}]: {site} joins no helper of {first} <-> {second} :: {detail}"
        )

    def analyze_file(self, file_path: str) -> List[RefactoringProposal]:
        """
        Analyze a Python file and find refactoring opportunities.

        Args:
            file_path: Path to Python file

        Returns:
            List of refactoring proposals
        """
        return self.analyze_files([file_path])

    def analyze_directory(
        self,
        directory: str,
        recursive: bool = True,
        *,
        progress: ProgressMode = DEFAULT_PROGRESS,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """
        Analyze all Python files in a directory and find refactoring opportunities.

        Args:
            directory: Path to directory
            recursive: Whether to search subdirectories (default: True)
            progress: How progress is shown (see ``ProgressMode``).
            changed_files: When given, only pairs with a function in one of
                these files are considered (see ``incremental_global_passes``).

        Returns:
            List of refactoring proposals
        """
        python_files = self._find_python_files(directory, recursive)

        if not python_files:
            return []

        LOG.debug("Found %d Python files in %s", len(python_files), directory)

        # Analyze all files together
        return self.analyze_files(python_files, progress=progress, changed_files=changed_files)

    def _find_python_files(self, directory: str, recursive: bool = True) -> List[str]:
        """
        Find all Python files in a directory.

        Args:
            directory: Directory to search
            recursive: Whether to search subdirectories

        Returns:
            List of Python file paths
        """
        return [
            str(path)
            for path in python_sources(
                Path(directory), recursive=recursive, excluded=self.excluded_directories
            )
        ]

    def analyze_files(
        self,
        file_paths: List[str],
        *,
        progress: ProgressMode = DEFAULT_PROGRESS,
        invalidate_paths: Optional[List[str]] = None,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Analyze multiple files using the compiler-style pipeline and return proposals.

        Args:
            file_paths: The files to analyze together.
            progress: How progress is shown (see ``ProgressMode``).
            invalidate_paths: Re-parse and re-analyze these paths even if cached.
            changed_files: When given, only pairs with a function in one of
                these files are considered (see ``incremental_global_passes``).
        """
        # Every file of the analysis must fit, or each pass re-parses them all.
        self.analysis_session.hold_at_least(
            len(file_paths), sum(_size_or_zero(path) for path in file_paths)
        )
        if self._output_origin is None:
            # Outside a fixed-point run the analysis is the run.
            self._forget_run_lookups()
        stale = {os.path.abspath(path) for path in (invalidate_paths or ())}
        stale.update(
            os.path.abspath(path) for path in file_paths if not self.analysis_session.reusable(path)
        )
        for path in stale:
            self._evict_cached_analysis(path)
        self._analysis_paths = tuple(file_paths)
        if self.cross_module_helpers and file_paths:
            # Read the program's imports before any pair is judged or a
            # worker forked, once for the project; a run's stage has them.
            program = self.import_graph.program_for(Path(file_paths[0]))
            # A file importing a module the tree lacks takes part in no pair.
            file_paths = [path for path in file_paths if not program.leaves_unchanged(Path(path))]
        return run_pipeline(
            file_paths,
            engine=self,
            progress=progress,
            invalidate_paths=invalidate_paths,
            session=self.analysis_session,
            changed_files=changed_files,
        )

    #: Entries kept per structural cache; oldest are dropped beyond this.
    STRUCTURAL_CACHE_LIMIT = 250_000
    #: Whole-file clustering scans kept; each holds every site of one template.
    CLUSTER_SCAN_CACHE_LIMIT = 4096
    #: Sites held across those scans: a thousand near-identical functions give
    #: every scan a thousand sites, and 4096 such scans held 8 GB.
    CLUSTER_SCAN_SITE_LIMIT = 200_000
    #: When a file's eviction-index list grows past this, drop entries the caches no longer hold.
    _EVICTION_INDEX_PRUNE_AT = 4096

    def _parse_source(self, source: str) -> ast.Module:
        """``ast.parse(source)``, remembered for the last few sources; callers never mutate the tree."""
        tree = self._parse_cache.get(source)
        if tree is None:
            tree = self._parse_cache.put(source, ast.parse(source))
        return tree

    def _sid(self, nodes: Sequence[ast.AST]) -> str:
        """The structural id of a contiguous block, or of a function, computed once.

        Memoized per (first node, length): the engine only asks about
        contiguous statement blocks and single functions, and two such
        sequences that start at one node and have one length are the same
        sequence. The memo is weak on the first node, so it needs no
        registration by path: a re-parsed file's old nodes take their
        entries with them.
        """
        if not nodes:
            return structural_id(nodes)
        by_length = self._structural_ids.get(nodes[0])
        if by_length is None:
            by_length = {}
            self._structural_ids[nodes[0]] = by_length
        cached = by_length.get(len(nodes))
        if cached is None:
            cached = structural_id(nodes)
            by_length[len(nodes)] = cached
        return cached

    def _module_digest(self, func: Optional[FunctionNode]) -> Optional[str]:
        """A digest of the module source a function came from, for module-wide analyses."""
        return self._function_sources.get(func) if func is not None else None

    def _block_site(self, func: FunctionNode, nodes: Sequence[ast.stmt]) -> Optional[BlockSite]:
        """Where ``nodes`` stand: their module, their function and their statements.

        None for a function this analysis did not record, or an empty block,
        which is then judged afresh on every call: without its module's
        digest nothing identifies what a verdict about it would depend on.
        """
        digest = self._module_digest(func)
        if digest is None or not nodes:
            return None
        first = nodes[0]
        return BlockSite(
            digest,
            (func.lineno, func.col_offset),
            (first.lineno, first.col_offset, len(nodes)),
        )

    def _remember(
        self, paths: Iterable[Optional[str]], cache: MutableMapping[Any, Any], key: Any
    ) -> None:
        """Register a cache entry under the files it depends on."""
        for path in paths:
            if path is not None:
                entries = self._cache_entries_by_path.setdefault(os.path.abspath(path), [])
                entries.append((cache, key))
                if len(entries) > self._EVICTION_INDEX_PRUNE_AT:
                    # Entries whose keys the bounded caches already dropped are
                    # dead weight; without this the index grew without bound.
                    entries[:] = [(c, k) for c, k in entries if k in c]

    def _evict_cached_analysis(self, path: str) -> None:
        """Drop every cache entry that depends on ``path``."""
        absolute = os.path.abspath(path)
        for cache, key in self._cache_entries_by_path.pop(absolute, ()):
            cache.pop(key, None)
        for function, function_path in tuple(self._function_paths.items()):
            if os.path.abspath(function_path) == absolute:
                del self._function_paths[function]
                self._function_sources.pop(function, None)

    def _function_index(self, all_functions: Sequence[FunctionArtifact]) -> FunctionIndex:
        """The index of ``all_functions``, built once and shared by every pair of the analysis.

        Keyed by the list's identity; the list is held alongside the index, so
        its identity cannot be recycled while the entry is live.
        """
        cached = self._function_index_cache
        if cached is None or cached[0] is not all_functions:
            cached = (all_functions, FunctionIndex.build(all_functions))
            self._function_index_cache = cached
        return cached[1]

    def _record_function_paths(self, all_functions: Sequence[FunctionArtifact]) -> None:
        for entry in all_functions:
            self._function_paths[entry.node] = entry.file_path
            self._function_sources[entry.node] = entry.module_digest

    def process_block_pairs(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        progress: ProgressMode,
    ) -> List[RefactoringProposal]:
        self._pair_rejections = {}
        if not block_pairs:
            return []

        self._seen_proposals.clear()
        self._settled_identities = set()
        self._record_function_paths(all_functions)
        if self._should_use_parallel(len(block_pairs)):
            return self._evaluate_pairs_parallel(
                block_pairs, all_functions, class_infos, progress=progress
            )
        return self._evaluate_pairs_serial(
            block_pairs,
            all_functions,
            class_infos,
            progress=progress,
        )

    def _judge_pair(
        self,
        pair: CodeBlockPair,
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
    ) -> Optional[RefactoringProposal]:
        """The pair's proposal, counting the reason when it is declined.

        A pair declined only because another pair already proposed the same
        refactoring loses nothing, so it is not counted. A pair declined
        without a traced reason is counted as ``other``, and a reason naming
        something is counted with it: ``decorator_may_transform_body[numba.njit]``.
        Pairs are judged here in their own order; forked evaluation judges
        them out of it (``_pair_verdict``) and settles them in it.
        """
        return self._settle(pair, self._pair_verdict(pair, all_functions, class_infos))

    def _pair_verdict(
        self,
        pair: CodeBlockPair,
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
    ) -> PairVerdict:
        """``pair`` judged, with its trace held back and nothing counted; see ``PairVerdict``."""
        self._pair_rejection = None
        self._pair_rejection_subject = None
        self._pair_identity = None
        self._trace_at_identity = 0
        captured: Optional[List[str]] = [] if debugging(REJECTIONS) else None
        self._captured_trace = captured
        try:
            proposal = self._try_refactor_pair_multi_file(pair, all_functions, class_infos)
        finally:
            self._captured_trace = None
        lines = tuple(captured or ())
        mark = len(lines) if self._pair_identity is None else self._trace_at_identity
        duplicate = proposal is None and self._pair_rejection is RejectReason.DUPLICATE_PROPOSAL
        return PairVerdict(
            proposal,
            None if self._pair_identity is None else identity_digest(self._pair_identity),
            None if proposal is not None or duplicate else self._rejection_key(),
            lines[:mark],
            lines[mark:],
        )

    def _settle(self, pair: CodeBlockPair, verdict: PairVerdict) -> Optional[RefactoringProposal]:
        """Trace and count ``verdict``, the next pair in this analysis's order; its proposal, if any.

        A pair whose proposal an earlier pair already settled is a duplicate,
        whatever its own judgement found beyond the duplicate check.
        """
        duplicate = verdict.identity is not None and verdict.identity in self._settled_identities
        if verdict.identity is not None:
            self._settled_identities.add(verdict.identity)
        for line in verdict.before_identity:
            self._trace_rejection(line)
        if duplicate:
            self._debug_reject(RejectReason.DUPLICATE_PROPOSAL, pair)
            return None
        for line in verdict.after_identity:
            self._trace_rejection(line)
        if verdict.counted is not None:
            self._pair_rejections[verdict.counted] = (
                self._pair_rejections.get(verdict.counted, 0) + 1
            )
        return verdict.proposal

    def _rejection_key(self) -> str:
        """What the pair just declined counts under: its reason, with what the reason names."""
        reason, subject = self._pair_rejection, self._pair_rejection_subject
        if reason is None:
            return "other"
        return str(reason) if subject is None else f"{reason}[{subject}]"

    @property
    def declined_pairs(self) -> Mapping[str, int]:
        """How many candidate pairs the latest analysis declined, by ``RejectReason``."""
        return dict(self._pair_rejections)

    def _allocate_helper_name(
        self,
        file_path: str,
        *,
        prefix: str,
        related_paths: Sequence[str] = (),
    ) -> str:
        """The next ``{prefix}_N`` for ``file_path``, past every number these files spell.

        The number is shared by every prefix and counts past any helper-shaped
        name in the file, the files the proposal touches, and every analysed
        file, so a name is unique across a run whatever its prefix.
        """

        counter = max(
            self._helper_name_counters.get(file_path, 0),
            *(
                self._discover_helper_counter_seed(path)
                for path in {file_path, *related_paths, *self._analysis_paths}
            ),
        )
        helper_name = f"{prefix}_{counter}"
        self._helper_name_counters[file_path] = counter + 1
        return helper_name

    def _discover_helper_counter_seed(self, file_path: str) -> int:
        """Prime the helper counter based on existing helper names in a file."""

        try:
            content = read_source(file_path)
        except (OSError, UnicodeError, SyntaxError):
            return 0

        pattern = re.compile(r"(?<!\w)_{1,2}extracted_func(?:_(\d+))?(?!\w)")
        max_seen = -1
        for match in pattern.finditer(content):
            suffix = match.group(1)
            idx = int(suffix) if suffix is not None else 0
            if idx > max_seen:
                max_seen = idx
        return max_seen + 1

    def find_block_pairs(
        self,
        all_functions: Sequence[FunctionArtifact],
        *,
        progress: ProgressMode = DEFAULT_PROGRESS,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[CodeBlockPair]:
        """
        Find every pair of similar code blocks in two different functions.

        The two functions share a file unless ``cross_module_helpers`` is
        set, when they may be anywhere in the analysis.

        Args:
            all_functions: The analyzed functions with their context
            progress: How progress is shown (see ``ProgressMode``).
            changed_files: When given, only pairs with a function in one of
                these files are considered (see ``incremental_global_passes``).

        Returns:
            List of code block pairs
        """

        self._record_function_paths(all_functions)
        # Read each project's coverage configuration here, before evaluation
        # forks, so a configuration that cannot be read is reported once.
        for path in sorted({entry.file_path for entry in all_functions}):
            self._coverage_exclusion(path)
        blocks = [self._signed_blocks(entry.node) for entry in all_functions]
        buckets = [_bucketed(function_blocks) for function_blocks in blocks]
        # Without cross-module helpers a function pairs only with the others
        # of its own module, so no pair across modules is formed, counted
        # against the budget, or evaluated.
        partners = _Partners.of(all_functions, self.cross_module_helpers)
        excluded = _buckets_over_budget(buckets, self.max_candidate_pairs, partners)
        if excluded:
            buckets = [bucket.without(excluded) for bucket in buckets]
        reporter = _PairingProgress(progress, partners.function_pairs)
        pairs: List[CodeBlockPair] = []
        # The changed set holds absolute paths; the analysis spells paths as
        # the caller gave them (a relative output directory stays relative).
        changed = (
            None if changed_files is None else {os.path.abspath(path) for path in changed_files}
        )
        for i, first in enumerate(all_functions):
            file1_changed = changed is None or os.path.abspath(first.file_path) in changed
            for j in partners.after(i):
                second = all_functions[j]
                if (
                    changed is not None
                    and not file1_changed
                    and os.path.abspath(second.file_path) not in changed
                ):
                    continue  # both files unchanged since the last global pass: verdict stands
                # Only blocks in a bucket the other function also has can pair;
                # the tolerance check is still the unchanged quick_filter.
                if not buckets[i].keys.isdisjoint(buckets[j].keys):
                    for (block1_range, block1_nodes, sig1), key1 in zip(
                        blocks[i], buckets[i].block_keys
                    ):
                        for block2_range, block2_nodes, sig2 in buckets[j].by_key.get(key1, []):
                            if not quick_filter(sig1, sig2):
                                continue
                            pairs.append(
                                CodeBlockPair(
                                    file_path=first.file_path,
                                    function1_name=first.node.name,
                                    function2_name=second.node.name,
                                    block1_range=block1_range,
                                    block2_range=block2_range,
                                    block1_nodes=block1_nodes,
                                    block2_nodes=block2_nodes,
                                    file_path2=second.file_path,
                                    class1_name=first.class_name,
                                    class2_name=second.class_name,
                                    enclosing_function1_name=first.enclosing_function,
                                    enclosing_function2_name=second.enclosing_function,
                                    function1_ancestry=first.ancestry,
                                    function2_ancestry=second.ancestry,
                                    scope_analyzer1=first.scope_analyzer,
                                    scope_analyzer2=second.scope_analyzer,
                                    root_scope1=first.root_scope,
                                    root_scope2=second.root_scope,
                                    source1=first.source,
                                    source2=second.source,
                                    function1_node=first.node,
                                    function2_node=second.node,
                                )
                            )
                reporter.function_pair_done(len(pairs))
        reporter.finish()
        return pairs

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------

    @property
    def change_log(self) -> Sequence[AppliedChange]:
        """Every helper call site the last fixed-point run rewrote, in application order.

        Both drivers reset it when they start. Every site is a call of a helper
        the run inserted, since no site is redirected to an existing function.
        """
        return tuple(self._change_log)

    def invalidate_paths(self, paths: List[str]) -> None:
        """Forget every analysis and cached source line of ``paths``; they were rewritten."""
        self.analysis_session.invalidate(paths)
        for path in paths:
            self._source_lines_cache.pop(path, None)
