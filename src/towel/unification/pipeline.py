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

from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Union, cast
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
            tree = ast.parse(src)
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
            qualname = (
                ".".join(self.class_stack + [node.name]) if self.class_stack else node.name
            )
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


def pair_blocks(engine: "UnificationRefactorEngine", funcs: Sequence[FunctionArtifact]) -> List[CodeBlockPair]:
    # Reuse the engine's pairing method by reconstructing the tuple format it expects
    packed = [
        (
            f.file_path,
            cast(ast.FunctionDef, f.node),
            f.source,
            f.scope_analyzer,
            f.root_scope,
            f.class_name,
            f.enclosing_function,
            f.ancestry,
        )
        for f in funcs
    ]
    return engine._find_block_pairs_multi_file(packed)


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
            cast(ast.FunctionDef, f.node),
            f.source,
            f.scope_analyzer,
            f.root_scope,
            f.class_name,
            f.enclosing_function,
            f.ancestry,
        )
        for f in funcs
    ]
    return engine._process_block_pairs(list(pairs), packed, list(classes), verbose=verbose, progress=progress)


def filter_overlaps(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    # Delegate to existing implementation for stability
    from .refactor_engine import filter_overlapping_proposals as _filter

    return _filter(proposals)


def run_pipeline(
    paths: Sequence[str],
    *,
    engine: Optional["UnificationRefactorEngine"] = None,
    verbose: bool = False,
    progress: str = "auto",
) -> List[RefactoringProposal]:
    """Run the full compiler-style pipeline for the given file paths.

    Returns the list of non-overlapping :class:`RefactoringProposal` objects.
    """
    # Lazy import to avoid circular import at module load time
    if engine is None:
        from .refactor_engine import UnificationRefactorEngine as _Engine  # local import

        eng: "UnificationRefactorEngine" = _Engine()
    else:
        eng = engine

    # Phase 1: parse
    mods = parse_modules(paths)

    # Phase 2: scopes
    analyze_scopes(mods)

    # Phase 3: class table
    classes = collect_classes(mods)

    # Phase 4: functions with context
    funcs = collect_functions(mods)

    # Phase 5: candidate pairing
    pairs = pair_blocks(eng, funcs)

    # Phase 6: unify to proposals
    props = unify_blocks(eng, pairs, funcs, classes, verbose=verbose, progress=progress)

    # Phase 7: de-overlap
    return filter_overlaps(props)
