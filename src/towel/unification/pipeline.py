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

"""
Compiler-style refactoring pipeline: sequential phases that transform ASTs and
carry auxiliary analysis artifacts forward.

Phases (in order):
1) parse_modules: str path -> ParsedModule (tree, source)
2) analyze_scopes: attach ScopeAnalyzer and root Scope to ParsedModule
3) collect_classes: build ClassInfo table per module
4) collect_functions: build FunctionArtifact list with enclosing class/function context
5) pair_blocks: enumerate CodeBlockPair candidates
6) unify_blocks: attempt unification and construct RefactoringProposal objects
7) filter_overlaps: remove overlapping/conflicting proposals

The top-level run_pipeline() wires these phases and optionally reuses isolated
module analyses through an explicitly owned AnalysisSession.
"""

from __future__ import annotations

from typing import (
    Dict,
    FrozenSet,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)
import ast
from collections import OrderedDict
import hashlib
from dataclasses import dataclass
import os

from .models import (
    ParsedModule,
    FunctionArtifact,
    ClassInfo,
    CodeBlockPair,
    RefactoringProposal,
)
from .scope_analyzer import ScopeAnalyzer
from .overlap import filter_overlapping_proposals
from .progress import (
    DEFAULT_PROGRESS,
    ProgressBar,
    ProgressMode,
    load_tqdm,
    quietly,
    render_inline_bar,
    wants_bar,
)
from ..diagnostics import LOG, Settings
from ..source_text import read_source
from .visitors import DefinitionDepthVisitor, FunctionCollector


class PairProcessor(Protocol):
    """The two phases the engine supplies: candidate pairing and pair evaluation.

    The pipeline depends on this protocol, never on the engine, so the engine
    can depend on the pipeline's parse and analysis phases without a cycle.
    """

    def find_block_pairs(
        self,
        all_functions: Sequence[FunctionArtifact],
        *,
        progress: ProgressMode = DEFAULT_PROGRESS,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[CodeBlockPair]:
        """Candidate block pairs across ``all_functions``."""
        ...

    def process_block_pairs(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: Sequence[FunctionArtifact],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: ProgressMode,
    ) -> List[RefactoringProposal]:
        """Verified proposals for the pairs that unify."""
        ...


def parse_modules(paths: Sequence[str]) -> List[ParsedModule]:
    modules: List[ParsedModule] = []
    for p in paths:
        try:
            src, tree = _read_and_normalize_module(p)
        except (OSError, UnicodeError, SyntaxError) as error:
            LOG.warning("Skipping %s: %s", p, error)
            continue
        modules.append(ParsedModule(file_path=p, source=src, tree=tree))
    return modules


def analyze_scopes(mods: Sequence[ParsedModule]) -> None:
    for m in mods:
        analyzer = ScopeAnalyzer()
        m.root_scope = analyzer.analyze(m.tree)
        m.scope_analyzer = analyzer


def _resolve_base_name(expr: ast.expr) -> Optional[str]:
    """The dotted name a base-class expression spells, or None for anything else."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        parts: List[str] = []
        cur: ast.expr = expr
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


class _ClassCollector(DefinitionDepthVisitor):
    """Appends a ClassInfo for every class of one module to ``infos``."""

    def __init__(self, file_path: str, infos: List[ClassInfo]) -> None:
        self.file_path = file_path
        self.infos = infos
        self.class_stack: List[str] = []

    def _enter_definition(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        if not isinstance(node, ast.ClassDef):
            return
        qualname = ".".join(self.class_stack + [node.name]) if self.class_stack else node.name
        bases = [
            resolved for resolved in map(_resolve_base_name, node.bases) if resolved is not None
        ]
        self.infos.append(
            ClassInfo(name=node.name, qualname=qualname, file_path=self.file_path, bases=bases)
        )
        self.class_stack.append(node.name)

    def _leave_definition(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]
    ) -> None:
        if isinstance(node, ast.ClassDef):
            self.class_stack.pop()


def collect_classes(mods: Sequence[ParsedModule]) -> List[ClassInfo]:
    infos: List[ClassInfo] = []
    for m in mods:
        _ClassCollector(m.file_path, infos).visit(m.tree)
    return infos


def collect_functions(mods: Sequence[ParsedModule]) -> List[FunctionArtifact]:
    funcs: List[FunctionArtifact] = []

    for mod in mods:
        if mod.scope_analyzer is None or mod.root_scope is None:
            raise RuntimeError("collect_functions requires analyze_scopes to run first")

        analyzer = mod.scope_analyzer
        root_scope = mod.root_scope

        def sink(
            node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
            class_name: Optional[str],
            enclosing_function: Optional[str],
            ancestry: List[str],
        ) -> None:
            funcs.append(
                FunctionArtifact(
                    file_path=mod.file_path,
                    node=node,
                    source=mod.source,
                    scope_analyzer=analyzer,
                    root_scope=root_scope,
                    class_name=class_name,
                    enclosing_function=enclosing_function,
                    ancestry=ancestry,
                )
            )

        FunctionCollector(sink).visit(mod.tree)
    return funcs


def pair_blocks(
    engine: PairProcessor,
    funcs: Sequence[FunctionArtifact],
    *,
    progress: ProgressMode = DEFAULT_PROGRESS,
    changed_files: Optional[FrozenSet[str]] = None,
) -> List[CodeBlockPair]:
    """Enumerate candidate block pairs with optional progress display.

    The engine owns candidate generation and its progress reporting; this adapter
    only packs the analyzed function context. ``changed_files`` restricts pairs
    to those with a function in one of them.
    """
    return engine.find_block_pairs(list(funcs), progress=progress, changed_files=changed_files)


def unify_blocks(
    engine: PairProcessor,
    pairs: Sequence[CodeBlockPair],
    funcs: Sequence[FunctionArtifact],
    classes: Sequence[ClassInfo],
    *,
    verbose: bool = False,
    progress: ProgressMode = DEFAULT_PROGRESS,
) -> List[RefactoringProposal]:
    return engine.process_block_pairs(
        list(pairs), list(funcs), list(classes), verbose=verbose, progress=progress
    )


def filter_overlaps(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    """Phase 7: keep a non-overlapping set of proposals (see ``overlap.py``)."""
    return filter_overlapping_proposals(proposals)


class SourceFileError(Exception):
    """An expected source read, decoding or syntax failure."""


@dataclass(frozen=True)
class ModuleAnalysis:
    """One internally consistent module/scope/function graph owned by its caller."""

    module: ParsedModule
    functions: Tuple[FunctionArtifact, ...]


class AnalysisSession:
    """Bounded analysis snapshots for one engine, with no process-global state.

    Entries use absolute paths plus the caller's spelling, preserving replacement
    paths while separating relative paths from different working directories.
    Source content is checked on every access. The source-byte budget bounds
    retained input, not the exact size of the Python object graph. Returned graphs
    are the cached objects themselves: analysis never mutates a module's AST, so
    an engine's node-identity caches stay valid for a file across fixed-point
    iterations that leave it unchanged. Set ``TOWEL_CHECK_AST_IMMUTABLE=1`` to
    verify that invariant on every reuse. Sessions belong to one analysis owner
    and are not shared between threads.
    """

    def __init__(self, *, max_entries: int = 128, max_source_bytes: int = 8 * 1024 * 1024) -> None:
        if max_entries < 0 or max_source_bytes < 0:
            raise ValueError("Analysis cache limits must be nonnegative")
        self._max_entries = max_entries
        self._max_source_bytes = max_source_bytes
        self._entries: OrderedDict[Tuple[str, str], ModuleAnalysis] = OrderedDict()
        self._source_bytes = 0
        self._check_immutable = Settings.from_environ().check_ast_immutable
        self._digests: Dict[Tuple[str, str], str] = {}

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def source_bytes(self) -> int:
        return self._source_bytes

    def clear(self) -> None:
        self._entries.clear()
        self._source_bytes = 0

    def _discard(self, key: Tuple[str, str]) -> None:
        previous = self._entries.pop(key, None)
        self._digests.pop(key, None)
        if previous is not None:
            self._source_bytes -= len(previous.module.source.encode("utf-8"))

    def reusable(self, path: str) -> bool:
        """Whether the next ``analyze_module(path)`` will return the cached graph."""
        cached = self._entries.get((os.path.abspath(path), path))
        if cached is None:
            return False
        try:
            return read_source(path) == cached.module.source
        except (OSError, UnicodeError, SyntaxError):
            return False

    @staticmethod
    def _digest(analysis: ModuleAnalysis) -> str:
        return hashlib.sha256(ast.dump(analysis.module.tree).encode("utf-8")).hexdigest()

    def invalidate(self, paths: Sequence[str]) -> None:
        """Discard every spelling of the selected absolute paths in this session."""
        absolute_paths = {os.path.abspath(path) for path in paths}
        for key in tuple(self._entries):
            if key[0] in absolute_paths:
                self._discard(key)

    def analyze_module(self, path: str) -> ModuleAnalysis:
        """Read current content and return an isolated, fully analyzed snapshot.

        Read, decoding and parse failures become SourceFileError for diagnostics.
        Analysis failures also propagate, because they indicate engine defects,
        not a conservative rejection of a source file.
        """
        key = (os.path.abspath(path), path)
        try:
            source = read_source(path)
        except (OSError, UnicodeError, SyntaxError) as error:
            self._discard(key)
            raise SourceFileError(str(error)) from error
        cached = self._entries.get(key)
        if cached is not None and cached.module.source == source:
            self._entries.move_to_end(key)
            if self._check_immutable and self._digest(cached) != self._digests[key]:
                raise RuntimeError(f"Analysis mutated the cached AST of {path}")
            return cached
        self._discard(key)
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as error:
            raise SourceFileError(str(error)) from error
        module = ParsedModule(file_path=path, source=source, tree=tree)
        analyze_scopes([module])
        module.class_infos = collect_classes([module])
        analysis = ModuleAnalysis(module, tuple(collect_functions([module])))
        source_bytes = len(source.encode("utf-8"))
        if self._max_entries and source_bytes <= self._max_source_bytes:
            while self._entries and (
                len(self._entries) >= self._max_entries
                or self._source_bytes + source_bytes > self._max_source_bytes
            ):
                self._discard(next(iter(self._entries)))
            self._entries[key] = analysis
            if self._check_immutable:
                self._digests[key] = self._digest(analysis)
            self._source_bytes += source_bytes
        return analysis


_PARSED: "OrderedDict[str, ast.Module]" = OrderedDict()
_PARSED_LIMIT = 64


def parse_cached(source: str) -> ast.Module:
    """``ast.parse(source)``, remembered for the last few sources.

    The apply path parses each modified file several times per proposal to
    check arity and find insertion points; the parse is pure in ``source``,
    so the tree is shared. Callers read it and never mutate it.
    """
    tree = _PARSED.get(source)
    if tree is None:
        tree = ast.parse(source)
        _PARSED[source] = tree
        while len(_PARSED) > _PARSED_LIMIT:
            _PARSED.popitem(last=False)
    else:
        _PARSED.move_to_end(source)
    return tree


def _read_and_normalize_module(path: str) -> Tuple[str, ast.AST]:
    """Read a module without changing Python operator or mutation semantics."""
    src = read_source(path)
    tree: ast.AST = ast.parse(src)
    return src, tree


def _create_progress_bar(
    use_progress: bool, total: int, desc: str, unit: str
) -> Optional[ProgressBar]:
    """Return a tqdm-style progress bar if tqdm is available, else None."""
    if not use_progress or total <= 0:
        return None
    tqdm_cls = load_tqdm()
    if tqdm_cls is None:
        return None
    try:
        return tqdm_cls(total=total, desc=desc, unit=unit, dynamic_ncols=True, leave=False)
    except Exception:
        return None


def _close_progress_bar(bar: Optional[ProgressBar]) -> None:
    if bar is not None:
        quietly(bar.close)


def run_pipeline(
    paths: Sequence[str],
    *,
    engine: PairProcessor,
    session: Optional[AnalysisSession] = None,
    verbose: bool = False,
    progress: ProgressMode = DEFAULT_PROGRESS,
    invalidate_paths: Optional[Sequence[str]] = None,
    changed_files: Optional[FrozenSet[str]] = None,
) -> List[RefactoringProposal]:
    """Analyze current files and propose changes using isolated analysis graphs.

    ``engine`` pairs and evaluates blocks (an ``UnificationRefactorEngine``).
    Standalone calls use a fresh session. Engines explicitly supply their own
    bounded session to reuse unchanged files. Invalidation forces fresh analysis;
    ordinary calls still read and compare source content before reusing an entry.
    """
    analysis_session = session if session is not None else AnalysisSession()
    if invalidate_paths:
        analysis_session.invalidate(invalidate_paths)

    use_progress = wants_bar(progress)
    bar = _create_progress_bar(use_progress, len(paths), "analyze", "file")
    inline_progress = use_progress and bar is None and len(paths) > 0
    if inline_progress:
        print("Analyzing files:", end=" ", flush=True)
    analyses: List[ModuleAnalysis] = []
    try:
        for index, path in enumerate(paths, 1):
            try:
                analyses.append(analysis_session.analyze_module(path))
            except SourceFileError as error:
                LOG.warning("Skipping %s: %s", path, error)
            if bar is not None:
                quietly(lambda: bar.update(1))
            elif inline_progress:
                percent = int(100 * index / len(paths))
                print(
                    f"\rAnalyzing files: [{render_inline_bar(percent)}] {percent:3d}%",
                    end="",
                    flush=True,
                )
    finally:
        _close_progress_bar(bar)
        if inline_progress:
            print()

    functions = [function for analysis in analyses for function in analysis.functions]
    classes = [info for analysis in analyses for info in analysis.module.class_infos]
    pairs = pair_blocks(engine, functions, progress=progress, changed_files=changed_files)
    proposals = unify_blocks(engine, pairs, functions, classes, verbose=verbose, progress=progress)
    return filter_overlaps(proposals)
