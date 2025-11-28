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

The top-level run_pipeline() wires these phases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union, cast
import ast
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
from .ast_normalizer import normalize_assigns_to_augassigns, canonicalize_arithmetic

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
            src = Path(p).read_text(encoding="utf-8")
            tree: ast.AST = ast.parse(src)
            tree = normalize_assigns_to_augassigns(tree)
            tree = canonicalize_arithmetic(tree)
        except Exception:
            # Skip unreadable or syntactically invalid files
            continue
        modules.append(ParsedModule(file_path=p, source=src, tree=tree))
    return modules


def analyze_scopes(mods: Sequence[ParsedModule]) -> None:
    for m in mods:
        analyzer = cast("ScopeAnalyzer", cast(Any, ScopeAnalyzer)())
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

    Shows early progress because pair enumeration can be the longest pre-unification step.
    We only enumerate function pairs here (O(n^2)) and defer actual block pairing to the
    engine to avoid logic duplication. This still provides a responsive bar so users see
    movement before proposals appear.
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

    # Delegate to engine when progress disabled for minimal overhead
    if progress not in {"tqdm", "auto"}:
        return engine._find_block_pairs_multi_file(packed)

    total_funcs = len(packed)
    total_func_pairs = (total_funcs * (total_funcs - 1)) // 2 if total_funcs > 1 else 0
    use_tqdm = False
    tqdm_bar = None
    if progress in {"tqdm", "auto"}:
        try:
            import importlib

            _tqdm_mod = importlib.import_module("tqdm.auto")
            _tqdm_cls = getattr(_tqdm_mod, "tqdm")
            tqdm_bar = _tqdm_cls(
                total=total_func_pairs,
                desc="pairing",
                unit="func-pair",
                dynamic_ncols=True,
                leave=False,
            )
            use_tqdm = True
        except Exception:
            use_tqdm = False

    # Inline fallback bar -------------------------------------------------
    use_inline = (not use_tqdm) and progress in {"tqdm", "auto"} and total_func_pairs > 0
    last_pct = -1
    if use_inline:
        print("Pairing function pairs:", end=" ", flush=True)

    # Instrumented pairing: we use a light wrapper around the engine's implementation
    # to stream progress metrics. We avoid deep changes inside the engine for stability.
    func_pairs_examined = 0
    for i in range(total_funcs):
        file1, func1, source1, analyzer1, scope1, class1, encl1, anc1 = packed[i]
        for j in range(i + 1, total_funcs):
            _ = packed[j]
            # We do not attempt to replicate pairing work here; this loop is only for progress.
            func_pairs_examined += 1
            if use_tqdm and tqdm_bar is not None:
                try:
                    tqdm_bar.update(1)
                    # Keep postfix compact to avoid wrapping
                    if func_pairs_examined % 50 == 0 or func_pairs_examined == total_func_pairs:
                        tqdm_bar.set_postfix(
                            {"fp": f"{func_pairs_examined}/{total_func_pairs}"}, refresh=True
                        )
                except Exception:
                    pass
            elif use_inline:
                pct = int(100 * func_pairs_examined / max(total_func_pairs, 1))
                if pct != last_pct:
                    last_pct = pct
                    bar_len = 24
                    filled = (pct * bar_len) // 100
                    bar = "#" * filled + "-" * (bar_len - filled)
                    print(
                        f"\rPairing function pairs: [{bar}] {pct:3d}% | scanned={func_pairs_examined}/{total_func_pairs}",
                        end="",
                        flush=True,
                    )
        # We intentionally do not attempt partial block pairing here to avoid duplicating logic.
        # Full construction performed once at end via engine call.
    # Finish inline bar line
    if use_inline:
        print()
    if use_tqdm and tqdm_bar is not None:
        try:
            tqdm_bar.close()
        except Exception:
            pass

    # Now perform actual pairing using engine logic (single call for correctness)
    # Defer to engine pairing (now instrumented internally for progress)
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


# Simple analysis cache keyed by file path
_analysis_cache: Dict[str, Dict[str, Any]] = {}
_ANALYSIS_CACHE_VERSION = 2


def run_pipeline(
    paths: Sequence[str],
    *,
    engine: Optional["UnificationRefactorEngine"] = None,
    verbose: bool = False,
    progress: str = "auto",
    invalidate_paths: Optional[Sequence[str]] = None,
) -> List[RefactoringProposal]:
    """
    Run the full compiler-style pipeline for the given file paths, using cached analysis results.
    If invalidate_paths is provided, only those files are reparsed/reanalyzed; others use cached results.
    """
    # Lazy import to avoid circular import at module load time
    if engine is None:
        from .refactor_engine import UnificationRefactorEngine as _Engine  # local import

        eng: "UnificationRefactorEngine" = _Engine()
    else:
        eng = engine

    # Invalidate cache for changed files
    if invalidate_paths:
        for p in invalidate_paths:
            _analysis_cache.pop(p, None)

    use_progress = progress in {"tqdm", "auto"}
    # --------------------- Phase 1: parse modules ---------------------
    mods = []
    if use_progress:
        try:
            import importlib

            _tqdm_mod = importlib.import_module("tqdm.auto")
            _tqdm_cls = getattr(_tqdm_mod, "tqdm")
            parse_bar = _tqdm_cls(
                total=len(paths), desc="parse", unit="file", dynamic_ncols=True, leave=False
            )
        except Exception:
            parse_bar = None
    else:
        parse_bar = None

    last_pct = -1
    inline_parse = use_progress and parse_bar is None and len(paths) > 0
    if inline_parse:
        print("Parsing files:", end=" ", flush=True)
    for idx, p in enumerate(paths, 1):
        cache_entry = _analysis_cache.get(p)
        if cache_entry and cache_entry.get("version") == _ANALYSIS_CACHE_VERSION:
            mods.append(cache_entry["mod"])
        else:
            try:
                src = Path(p).read_text(encoding="utf-8")
                tree: ast.AST = ast.parse(src)
                tree = normalize_assigns_to_augassigns(tree)
                tree = canonicalize_arithmetic(tree)
                mod = ParsedModule(file_path=p, source=src, tree=tree)
                _analysis_cache[p] = {"mod": mod, "version": _ANALYSIS_CACHE_VERSION}
                mods.append(mod)
            except Exception:
                continue
        if parse_bar is not None:
            try:
                parse_bar.update(1)
            except Exception:
                pass
        elif inline_parse:
            pct = int(100 * idx / len(paths))
            if pct != last_pct:
                last_pct = pct
                bar_len = 24
                filled = (pct * bar_len) // 100
                bar = "#" * filled + "-" * (bar_len - filled)
                print(f"\rParsing files: [{bar}] {pct:3d}%", end="", flush=True)
    if parse_bar is not None:
        try:
            parse_bar.close()
        except Exception:
            pass
    if inline_parse:
        print()

    # --------------------- Phase 2: scope analysis ---------------------
    if use_progress:
        try:
            import importlib

            _tqdm_mod = importlib.import_module("tqdm.auto")
            _tqdm_cls = getattr(_tqdm_mod, "tqdm")
            scope_bar = _tqdm_cls(
                total=len(mods), desc="scope", unit="mod", dynamic_ncols=True, leave=False
            )
        except Exception:
            scope_bar = None
    else:
        scope_bar = None
    inline_scope = use_progress and scope_bar is None and len(mods) > 0
    last_pct = -1
    if inline_scope:
        print("Analyzing scopes:", end=" ", flush=True)
    for idx, m in enumerate(mods, 1):
        if "scoped" not in _analysis_cache[m.file_path]:
            analyzer = cast("ScopeAnalyzer", cast(Any, ScopeAnalyzer)())
            m.root_scope = analyzer.analyze(m.tree)
            m.scope_analyzer = analyzer
            _analysis_cache[m.file_path]["scoped"] = True
        else:
            # Reattach analyzer/root_scope from cache for downstream phases
            cached_mod = _analysis_cache[m.file_path]["mod"]
            m.root_scope = cached_mod.root_scope
            m.scope_analyzer = cached_mod.scope_analyzer
        if scope_bar is not None:
            try:
                scope_bar.update(1)
            except Exception:
                pass
        elif inline_scope:
            pct = int(100 * idx / len(mods))
            if pct != last_pct:
                last_pct = pct
                bar_len = 24
                filled = (pct * bar_len) // 100
                bar = "#" * filled + "-" * (bar_len - filled)
                print(f"\rAnalyzing scopes: [{bar}] {pct:3d}%", end="", flush=True)
    if scope_bar is not None:
        try:
            scope_bar.close()
        except Exception:
            pass
    if inline_scope:
        print()

    # --------------------- Phase 3: class collection ---------------------
    if use_progress:
        try:
            import importlib

            _tqdm_mod = importlib.import_module("tqdm.auto")
            _tqdm_cls = getattr(_tqdm_mod, "tqdm")
            class_bar = _tqdm_cls(
                total=len(mods), desc="class", unit="mod", dynamic_ncols=True, leave=False
            )
        except Exception:
            class_bar = None
    else:
        class_bar = None
    inline_class = use_progress and class_bar is None and len(mods) > 0
    last_pct = -1
    if inline_class:
        print("Collecting classes:", end=" ", flush=True)
    for idx, m in enumerate(mods, 1):
        if "classes" not in _analysis_cache[m.file_path]:
            infos = collect_classes([m])
            m.class_infos = infos
            _analysis_cache[m.file_path]["classes"] = infos
        else:
            m.class_infos = _analysis_cache[m.file_path]["classes"]
        if class_bar is not None:
            try:
                class_bar.update(1)
            except Exception:
                pass
        elif inline_class:
            pct = int(100 * idx / len(mods))
            if pct != last_pct:
                last_pct = pct
                bar_len = 24
                filled = (pct * bar_len) // 100
                bar = "#" * filled + "-" * (bar_len - filled)
                print(f"\rCollecting classes: [{bar}] {pct:3d}%", end="", flush=True)
    if class_bar is not None:
        try:
            class_bar.close()
        except Exception:
            pass
    if inline_class:
        print()

    classes: List[ClassInfo] = []
    for m in mods:
        classes.extend(m.class_infos or [])

    # --------------------- Phase 4: function collection ---------------------
    if use_progress:
        try:
            import importlib

            _tqdm_mod = importlib.import_module("tqdm.auto")
            _tqdm_cls = getattr(_tqdm_mod, "tqdm")
            func_bar = _tqdm_cls(
                total=len(mods), desc="func", unit="mod", dynamic_ncols=True, leave=False
            )
        except Exception:
            func_bar = None
    else:
        func_bar = None
    inline_func = use_progress and func_bar is None and len(mods) > 0
    last_pct = -1
    if inline_func:
        print("Collecting functions:", end=" ", flush=True)
    for idx, m in enumerate(mods, 1):
        if "funcs" not in _analysis_cache[m.file_path]:
            funcs_list = collect_functions([m])
            _analysis_cache[m.file_path]["funcs"] = funcs_list
        else:
            funcs_list = _analysis_cache[m.file_path]["funcs"]
        if func_bar is not None:
            try:
                func_bar.update(1)
                if idx == len(mods):
                    func_bar.set_postfix(
                        {
                            "total": sum(
                                len(_analysis_cache[x]["funcs"])
                                for x in _analysis_cache
                                if "funcs" in _analysis_cache[x]
                            )
                        },
                        refresh=True,
                    )
            except Exception:
                pass
        elif inline_func:
            pct = int(100 * idx / len(mods))
            if pct != last_pct:
                last_pct = pct
                bar_len = 24
                filled = (pct * bar_len) // 100
                bar = "#" * filled + "-" * (bar_len - filled)
                print(f"\rCollecting functions: [{bar}] {pct:3d}%", end="", flush=True)
    if func_bar is not None:
        try:
            func_bar.close()
        except Exception:
            pass
    if inline_func:
        print()

    funcs: List[FunctionArtifact] = []
    for m in mods:
        funcs.extend(_analysis_cache[m.file_path]["funcs"])

    # Phase 5: candidate pairing (not cached; depends on all funcs)
    pairs = pair_blocks(eng, funcs, progress=progress)

    # Phase 6: unify to proposals (not cached; depends on all pairs/classes)
    props = unify_blocks(eng, pairs, funcs, classes, verbose=verbose, progress=progress)

    # Phase 7: de-overlap
    return filter_overlaps(props)
