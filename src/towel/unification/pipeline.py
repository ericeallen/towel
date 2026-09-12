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
    TYPE_CHECKING,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
    Protocol,
    Mapping,
)
import ast
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import sys
import os
from pathlib import Path

from .models import (
    ParsedModule,
    FunctionArtifact,
    ClassInfo,
    CodeBlockPair,
    RefactoringProposal,
)
from .scope_analyzer import ScopeAnalyzer
from .visitors import FunctionCollector

if TYPE_CHECKING:  # pragma: no cover
    from .refactor_engine import UnificationRefactorEngine


# The current engine already implements substantial logic. This module orchestrates
# a clean compiler-style sequence by delegating to the engine for heavy lifting while
# making explicit the inputs/outputs between phases. This keeps public API stable
# and allows future migration of inner logic into dedicated phase modules.


def parse_modules(paths: Sequence[str]) -> List[ParsedModule]:
    modules: List[ParsedModule] = []
    for p in paths:
        try:
            src, tree = _read_and_normalize_module(p)
        except (OSError, UnicodeError, SyntaxError) as error:
            print(f"Skipping {p}: {error}", file=sys.stderr)
            continue
        modules.append(ParsedModule(file_path=p, source=src, tree=tree))
    return modules


def analyze_scopes(mods: Sequence[ParsedModule]) -> None:
    for m in mods:
        analyzer = ScopeAnalyzer()
        m.root_scope = analyzer.analyze(m.tree)
        m.scope_analyzer = analyzer


def collect_classes(mods: Sequence[ParsedModule]) -> List[ClassInfo]:
    infos: List[ClassInfo] = []

    def resolve_base_name(expr: ast.expr) -> Optional[str]:
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

    class Collector(ast.NodeVisitor):
        def __init__(self, file_path: str) -> None:
            self.file_path = file_path
            self.class_stack: List[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            qualname = ".".join(self.class_stack + [node.name]) if self.class_stack else node.name
            bases: List[str] = []
            for b in node.bases:
                resolved = resolve_base_name(b)
                if resolved:
                    bases.append(resolved)
            infos.append(
                ClassInfo(name=node.name, qualname=qualname, file_path=self.file_path, bases=bases)
            )
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

    for m in mods:
        Collector(m.file_path).visit(m.tree)
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
    engine: "UnificationRefactorEngine",
    funcs: Sequence[FunctionArtifact],
    *,
    progress: str = "none",
) -> List[CodeBlockPair]:
    """Enumerate candidate block pairs with optional progress display.

    The engine owns candidate generation and its progress reporting; this adapter
    only packs the analyzed function context.
    """
    packed = [
        (
            f.file_path,
            f.node,
            f.source,
            f.scope_analyzer,
            f.root_scope,
            f.class_name,
            f.enclosing_function,
            f.ancestry,
        )
        for f in funcs
    ]

    return engine._find_block_pairs_multi_file(packed, progress=progress)


def unify_blocks(
    engine: "UnificationRefactorEngine",
    pairs: Sequence[CodeBlockPair],
    funcs: Sequence[FunctionArtifact],
    classes: Sequence[ClassInfo],
    *,
    verbose: bool = False,
    progress: str = "auto",
) -> List[RefactoringProposal]:
    packed = [
        (
            f.file_path,
            f.node,
            f.source,
            f.scope_analyzer,
            f.root_scope,
            f.class_name,
            f.enclosing_function,
            f.ancestry,
        )
        for f in funcs
    ]
    return engine._process_block_pairs(
        list(pairs), packed, list(classes), verbose=verbose, progress=progress
    )


def filter_overlaps(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    # Delegate to existing implementation for stability
    from .refactor_engine import filter_overlapping_proposals as _filter

    return _filter(proposals)


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
    are independent copies: modifying proposals or analysis cannot poison reuse.
    Sessions belong to one analysis owner and are not shared between threads.
    """

    def __init__(self, *, max_entries: int = 128, max_source_bytes: int = 8 * 1024 * 1024) -> None:
        if max_entries < 0 or max_source_bytes < 0:
            raise ValueError("Analysis cache limits must be nonnegative")
        self._max_entries = max_entries
        self._max_source_bytes = max_source_bytes
        self._entries: OrderedDict[Tuple[str, str], ModuleAnalysis] = OrderedDict()
        self._source_bytes = 0

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
        if previous is not None:
            self._source_bytes -= len(previous.module.source.encode("utf-8"))

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
            source = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            self._discard(key)
            raise SourceFileError(str(error)) from error
        cached = self._entries.get(key)
        if cached is not None and cached.module.source == source:
            self._entries.move_to_end(key)
            return deepcopy(cached)
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
            # Snapshot before the caller can populate node-identity caches or
            # mutate ASTs; deepcopy preserves graph identity across all artifacts.
            self._entries[key] = deepcopy(analysis)
            self._source_bytes += source_bytes
        return analysis


def _read_and_normalize_module(path: str) -> Tuple[str, ast.AST]:
    """Read a module without changing Python operator or mutation semantics."""
    src = Path(path).read_text(encoding="utf-8")
    tree: ast.AST = ast.parse(src)
    return src, tree


class ProgressBar(Protocol):
    def update(self, n: int = 1) -> object:
        """Advance the displayed progress."""
        ...

    def close(self) -> object:
        """Finish the progress display."""
        ...

    def set_postfix(self, ordered_dict: Mapping[str, object], refresh: bool = True) -> object:
        """Display compact progress details."""
        ...


class ProgressFactory(Protocol):
    def __call__(
        self, *, total: int, desc: str, unit: str, dynamic_ncols: bool, leave: bool
    ) -> ProgressBar:
        """Construct a progress display without requiring tqdm at runtime."""
        ...


def _get_tqdm_class() -> Optional[ProgressFactory]:
    """Dynamically import tqdm.auto.tqdm if available."""
    try:
        import importlib

        tqdm_mod = importlib.import_module("tqdm.auto")
        return cast(ProgressFactory, getattr(tqdm_mod, "tqdm"))
    except Exception:
        return None


def _create_progress_bar(
    use_progress: bool, total: int, desc: str, unit: str
) -> Optional[ProgressBar]:
    """Return a tqdm-style bar if available (see docs/DRY_RUN_2025-11-28.md)."""
    if not use_progress or total <= 0:
        return None
    tqdm_cls = _get_tqdm_class()
    if tqdm_cls is None:
        return None
    try:
        return tqdm_cls(total=total, desc=desc, unit=unit, dynamic_ncols=True, leave=False)
    except Exception:
        return None


def _render_inline_bar(pct: int, bar_len: int = 24) -> str:
    pct = max(0, min(100, pct))
    filled = (pct * bar_len) // 100
    return "#" * filled + "-" * (bar_len - filled)


def _close_progress_bar(bar: Optional[ProgressBar]) -> None:
    if bar is None:
        return
    try:
        bar.close()
    except Exception:
        pass


def run_pipeline(
    paths: Sequence[str],
    *,
    engine: Optional["UnificationRefactorEngine"] = None,
    session: Optional[AnalysisSession] = None,
    verbose: bool = False,
    progress: str = "auto",
    invalidate_paths: Optional[Sequence[str]] = None,
) -> List[RefactoringProposal]:
    """Analyze current files and propose changes using isolated analysis graphs.

    Standalone calls use a fresh session. Engines explicitly supply their own
    bounded session to reuse unchanged files. Invalidation forces fresh analysis;
    ordinary calls still read and compare source content before reusing an entry.
    """
    if engine is None:
        from .refactor_engine import UnificationRefactorEngine

        engine = UnificationRefactorEngine()
    analysis_session = session if session is not None else AnalysisSession()
    if invalidate_paths:
        analysis_session.invalidate(invalidate_paths)

    use_progress = progress in {"tqdm", "auto"}
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
                print(f"Skipping {path}: {error}", file=sys.stderr)
            if bar is not None:
                try:
                    bar.update(1)
                except Exception:
                    # A display failure cannot change analysis outcomes.
                    pass
            elif inline_progress:
                percent = int(100 * index / len(paths))
                print(
                    f"\rAnalyzing files: [{_render_inline_bar(percent)}] {percent:3d}%",
                    end="",
                    flush=True,
                )
    finally:
        _close_progress_bar(bar)
        if inline_progress:
            print()

    functions = [function for analysis in analyses for function in analysis.functions]
    classes = [info for analysis in analyses for info in analysis.module.class_infos]
    pairs = pair_blocks(engine, functions, progress=progress)
    proposals = unify_blocks(engine, pairs, functions, classes, verbose=verbose, progress=progress)
    return filter_overlaps(proposals)
