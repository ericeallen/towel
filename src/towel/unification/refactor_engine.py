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
Main refactoring engine using unification.

This orchestrates the entire refactoring process:
1. Parse files into ASTs
2. Find pairs of code blocks in top-level functions
3. Attempt unification to find parameterizable differences
4. Extract functions hygienically if unification succeeds
5. Generate replacement calls
"""

import ast
import copy
import os
import re
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, List, Tuple, Dict, Set, Optional, FrozenSet, Literal, Union, Sequence, cast
from weakref import WeakKeyDictionary
from pathlib import Path
from .scope_analyzer import ScopeAnalyzer, Scope
from .unifier import Unifier
from .extractor import HygienicExtractor, is_value_producing
from .orphan_detector import has_orphaned_variables
from .assignment_analyzer import (
    analyze_assignments,
    has_reassignments_without_bindings,
    _collect_block_binding_stats,
    _collect_bindings_and_reassignments,
)
from .project_layout import ProjectLayout
from .block_signature import extract_block_signature, quick_filter
from .models import (
    CodeBlockPair,
    MethodInfo,
    ClassInfo,
    ClassInsertionPlan,
    Replacement,
    RefactoringProposal,
)
from .pipeline import run_pipeline
from .visitors import (
    MethodCallRewriter,
    LoopReturnFinder,
    NameCollector,
    AugAssignFinder,
    AssignTargetVisitor,
    ClassLocator,
    FuncLocator,
)

# Configuration defaults
DEFAULT_MAX_PARAMETERS = 5
DEFAULT_MIN_LINES = 4
DEFAULT_SIMILARITY_THRESHOLD = 0.6
DEFAULT_MAX_ITERATIONS = 0  # Unlimited

FunctionNode = Union[ast.FunctionDef, ast.AsyncFunctionDef]


@dataclass(frozen=True)
class BlockBindingSnapshot:
    """Summarized binding data for a block, mirroring DRY helper structure."""

    bound_in_block: Set[str]
    reassigned_in_block: Set[str]
    bound_before_block: Set[str]
    bound_after_block: Set[str]
    initially_bound: Set[str]


_worker_engine: Optional["UnificationRefactorEngine"] = None
_worker_functions: Optional[
    List[
        Tuple[
            str,
            FunctionNode,
            str,
            "ScopeAnalyzer",
            "Scope",
            Optional[str],
            Optional[str],
            List[str],
        ]
    ]
] = None
_worker_class_infos: Optional[List[ClassInfo]] = None


def _initialize_pair_worker(
    engine_config: Dict[str, Any],
    functions: List[
        Tuple[str, FunctionNode, str, ScopeAnalyzer, Scope, Optional[str], Optional[str], List[str]]
    ],
    class_infos: List[ClassInfo],
) -> None:
    """Initializer that primes each worker process with engine state and function context."""

    global _worker_engine, _worker_functions, _worker_class_infos

    max_params = engine_config["max_parameters"]
    min_ln = engine_config["min_lines"]
    _worker_engine = UnificationRefactorEngine(
        max_parameters=int(max_params) if max_params is not None else DEFAULT_MAX_PARAMETERS,
        min_lines=int(min_ln) if min_ln is not None else DEFAULT_MIN_LINES,
        parameterize_constants=bool(engine_config["parameterize_constants"]),
        prefer_absolute_imports=engine_config.get("prefer_absolute_imports"),
        pep420_namespace_packages=engine_config.get("pep420_namespace_packages"),
    )
    _worker_functions = functions
    _worker_class_infos = class_infos


def _process_pair_in_worker(
    task: Tuple[int, CodeBlockPair],
) -> Tuple[int, Optional[RefactoringProposal]]:
    """Worker entry point that evaluates a single code block pair."""

    if _worker_engine is None or _worker_functions is None or _worker_class_infos is None:
        raise RuntimeError("Worker not initialized for pair processing")

    pair_index, pair = task
    proposal = _worker_engine._try_refactor_pair_multi_file(
        pair, _worker_functions, _worker_class_infos
    )
    return pair_index, proposal


class UnificationRefactorEngine:
    """
    Main engine for unification-based refactoring.

    This finds and extracts duplicate code using unification.
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
    ):
        """
        Initialize the refactoring engine.

        Args:
            max_parameters: Maximum parameters for extracted functions (default: 5)
            min_lines: Minimum lines for a code block (default: 4)
            parameterize_constants: Whether to parameterize differing constants
        """
        self.max_parameters = max_parameters
        self.min_lines = min_lines
        self.parameterize_constants = parameterize_constants
        self.unifier = Unifier(
            max_parameters=max_parameters,
            parameterize_constants=parameterize_constants,
            promote_equal_hof_literals=promote_equal_hof_literals,
        )
        self.extractor = HygienicExtractor()
        # Cross-file import preferences
        self.prefer_absolute_imports = prefer_absolute_imports
        self.pep420_namespace_packages = pep420_namespace_packages
        # Default behavior: allow safe handling of globals/nonlocals by not parameterizing
        # them and promoting necessary declarations into the extracted function when needed.

        # Memoization caches keyed by the identity of AST nodes parsed for this engine run.
        self._assignment_cache: WeakKeyDictionary[ast.AST, Dict[int, bool]] = WeakKeyDictionary()
        self._used_names_cache: WeakKeyDictionary[ast.AST, FrozenSet[str]] = WeakKeyDictionary()
        # Track helper name allocation per canonical file so helpers remain unique.
        self._helper_name_counters: Dict[str, int] = {}

    # --- Debug helpers ---
    def _debug_reject(
        self, reason: str, pair: "CodeBlockPair", detail: Optional[str] = None
    ) -> None:
        """Emit a concise rejection line when DEBUG_PROPOSAL_REJECTIONS is set.

        Includes function names and basic block ranges to help triage pruning gates.
        """
        try:
            import os as _os

            if not _os.getenv("DEBUG_PROPOSAL_REJECTIONS"):
                return
            msg = (
                f"REJECT[{reason}]: {pair.function1_name}{'@'+str(pair.block1_range) if pair.block1_range else ''} "
                f"<-> {pair.function2_name}{'@'+str(pair.block2_range) if pair.block2_range else ''}"
            )
            if detail:
                msg += f" :: {detail}"
            print(msg)
        except Exception:
            # Never let debug logging interfere with refactoring
            pass

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
        verbose: bool = False,
        progress: str = "tqdm",
    ) -> List[RefactoringProposal]:
        """
        Analyze all Python files in a directory and find refactoring opportunities.

        Args:
            directory: Path to directory
            recursive: Whether to search subdirectories (default: True)

        Returns:
            List of refactoring proposals
        """
        # Find all Python files
        python_files = self._find_python_files(directory, recursive)

        if not python_files:
            return []

        if verbose:
            print(f"Found {len(python_files)} Python files in {directory}")

        # Analyze all files together
        return self.analyze_files(python_files, verbose=verbose, progress=progress)

    def _find_python_files(self, directory: str, recursive: bool = True) -> List[str]:
        """
        Find all Python files in a directory.

        Args:
            directory: Directory to search
            recursive: Whether to search subdirectories

        Returns:
            List of Python file paths
        """
        python_files = []
        directory_path = Path(directory)

        if not directory_path.exists():
            return []

        if recursive:
            # Recursively find all .py files
            for py_file in directory_path.rglob("*.py"):
                # Skip common directories to ignore
                if any(
                    part.startswith(".") or part in ["__pycache__", "venv", "env", "node_modules"]
                    for part in py_file.parts
                ):
                    continue
                python_files.append(str(py_file))
        else:
            # Only find .py files in this directory
            for py_file in directory_path.glob("*.py"):
                python_files.append(str(py_file))

        return sorted(python_files)

    def _get_assignment_reuse(self, func: FunctionNode) -> Dict[int, bool]:
        """Return (and cache) assignment analysis for a function definition."""
        cached = self._assignment_cache.get(func)
        if cached is not None:
            return cached
        analysis = analyze_assignments(func)
        self._assignment_cache[func] = analysis
        return analysis

    def analyze_files(
        self,
        file_paths: List[str],
        *,
        verbose: bool = False,
        progress: str = "tqdm",
        invalidate_paths: Optional[List[str]] = None,
    ) -> List[RefactoringProposal]:
        """Analyze multiple files using the compiler-style pipeline and return proposals.

        invalidate_paths: If provided, forces reparse/reanalysis of these paths even if cached.
        """
        # Delegate to the pipeline for analysis while preserving existing behavior
        return run_pipeline(
            file_paths,
            engine=self,
            verbose=verbose,
            progress=progress,
            invalidate_paths=invalidate_paths,
        )

    def _process_block_pairs(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: List[
            Tuple[
                str,
                FunctionNode,
                str,
                ScopeAnalyzer,
                Scope,
                Optional[str],
                Optional[str],
                List[str],
            ]
        ],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: str,
    ) -> List[RefactoringProposal]:
        if not block_pairs:
            return []

        if self._should_use_parallel(len(block_pairs)):
            try:
                return self._evaluate_pairs_parallel(
                    block_pairs,
                    all_functions,
                    class_infos,
                    verbose=verbose,
                    progress=progress,
                )
            except Exception:
                if verbose:
                    print("Parallel pair evaluation failed; falling back to serial execution")
                # Fall back to serial evaluation if multiprocessing encounters an issue
                return self._evaluate_pairs_serial(
                    block_pairs,
                    all_functions,
                    class_infos,
                    verbose=verbose,
                    progress=progress,
                )

        return self._evaluate_pairs_serial(
            block_pairs,
            all_functions,
            class_infos,
            verbose=verbose,
            progress=progress,
        )

    def _should_use_parallel(self, pair_count: int) -> bool:
        """Decide if multiprocessing should be used for pair evaluation.

        Benchmarks run in Nov 2025 showed the ProcessPoolExecutor path to be
        roughly 0.6× slower than the serial evaluator because the work per
        candidate is too small to amortize process start and AST pickling cost.
        We keep this guard so future batching/tuning work can flip the switch
        without reworking the call site, but for now we always stay serial.
        """
        return False

    @staticmethod
    def _load_tqdm_wrapper() -> Optional[Any]:
        """Best-effort tqdm importer (mirrors DRY run helper, see docs/DRY_RUN_2025-11-28.md)."""

        try:
            import importlib

            module = importlib.import_module("tqdm.auto")
            return getattr(module, "tqdm")
        except Exception:
            return None

    @staticmethod
    def _start_inline_status(label: str, enabled: bool) -> None:
        """Emit the leading inline progress label when requested (DRY helper)."""

        if enabled:
            print(label, end=" ", flush=True)

    @staticmethod
    def _render_inline_bar(pct: int, bar_len: int = 24) -> str:
        """Render a textual progress bar reused across inline progress sites."""

        filled = (pct * bar_len) // 100
        return "#" * filled + "-" * (bar_len - filled)

    @classmethod
    def _update_inline_status(
        cls, label: str, pct: int, *, bar_len: int = 24, suffix: str = ""
    ) -> None:
        """Print an inline progress update with consistent formatting."""

        bar = cls._render_inline_bar(pct, bar_len=bar_len)
        suffix_text = f" {suffix}" if suffix else ""
        print(f"\r{label} [{bar}] {pct:3d}%{suffix_text}", end="", flush=True)

    @staticmethod
    def _finish_inline_status(enabled: bool) -> None:
        """Terminate the inline status line so subsequent logs stay readable."""

        if enabled:
            print()

    @staticmethod
    def _pop_next_proposal(queue: List[RefactoringProposal]) -> Optional[RefactoringProposal]:
        """Remove and return the oldest queued proposal (DRY helper; see docs/DRY_RUN_2025-11-28.md)."""

        if not queue:
            return None
        return queue.pop(0)

    def _resolve_progress_backend(self, progress: str) -> Tuple[str, Optional[Any], bool]:
        """Normalize progress flag and load tqdm when available (mirrors DRY helper guidance)."""

        allowed = {"auto", "tqdm", "none", "detail"}
        normalized = progress if progress in allowed else "tqdm"
        use_tqdm = normalized in {"auto", "tqdm"}
        tqdm_cls: Optional[Any] = None
        if use_tqdm:
            tqdm_cls = self._load_tqdm_wrapper()
            use_tqdm = tqdm_cls is not None
        return normalized, tqdm_cls, use_tqdm

    @staticmethod
    def _function_contains_nonlocal(func: FunctionNode) -> bool:
        """Return True if the function body contains any nonlocal declarations."""

        for stmt in func.body:
            # Skip nested definitions; only consider nonlocal statements that belong
            # to the function itself. Nonlocals inside nested functions do not impact
            # whether the outer function may be safely extracted.
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Nonlocal):
                    return True
        return False

    @staticmethod
    def _decorator_name(decorator: ast.expr) -> Optional[str]:
        """Return the simple name for a decorator expression if it can be resolved."""

        if isinstance(decorator, ast.Name):
            return decorator.id
        if isinstance(decorator, ast.Attribute):
            return decorator.attr
        if isinstance(decorator, ast.Call):
            return UnificationRefactorEngine._decorator_name(decorator.func)
        return None

    @staticmethod
    def _has_decorator(fn: ast.FunctionDef, name: str) -> bool:
        """Return True when the function already carries a decorator with the given name."""

        return any(
            UnificationRefactorEngine._decorator_name(dec) == name for dec in fn.decorator_list
        )

    @staticmethod
    def _strip_decorator(fn: ast.FunctionDef, name: str) -> None:
        """Remove any decorator whose resolved name matches ``name``."""

        fn.decorator_list = [
            dec
            for dec in fn.decorator_list
            if UnificationRefactorEngine._decorator_name(dec) != name
        ]

    @staticmethod
    def _ensure_leading_param(fn: ast.FunctionDef, param_name: str) -> None:
        """Ensure the positional-args list starts with ``param_name`` (preserving annotations)."""

        existing: Optional[ast.arg] = None
        remaining: List[ast.arg] = []
        for arg in fn.args.args:
            if arg.arg == param_name and existing is None:
                existing = arg
                continue
            if arg.arg == param_name:
                # Drop duplicate occurrences beyond the first
                continue
            remaining.append(arg)

        if existing is None:
            existing = ast.arg(arg=param_name)

        fn.args.args = [existing] + remaining

    @staticmethod
    def _retarget_helper_calls(node: ast.AST, original_name: str, final_name: str) -> ast.AST:
        """Rewrite helper call-sites when the extracted helper is renamed (DRY helper parity)."""

        if original_name == final_name:
            return node

        class _CallRenamer(ast.NodeTransformer):
            def __init__(self, old: str, new: str) -> None:
                self.old = old
                self.new = new

            def visit_Call(
                self, call: ast.Call
            ) -> ast.AST:  # pragma: no cover - simple AST rewrite
                updated = cast(ast.Call, self.generic_visit(call))
                if isinstance(updated.func, ast.Name) and updated.func.id == self.old:
                    updated.func.id = self.new
                return updated

        return _CallRenamer(original_name, final_name).visit(node)

    @staticmethod
    def _scan_module_docstring_and_imports(lines: List[str]) -> Tuple[int, int]:
        """Return last import line and docstring boundary (DRY helper parity)."""

        in_docstring = False
        docstring_char: Optional[str] = None
        last_import_line = 0
        after_docstring = 0

        for i, line in enumerate(lines):
            stripped = line.strip()

            if i == 0 and (stripped.startswith('"""') or stripped.startswith("'''")):
                docstring_char = stripped[:3]
                if stripped.count(docstring_char) < 2:
                    in_docstring = True
                else:
                    after_docstring = i + 1
                continue

            if in_docstring:
                assert docstring_char is not None
                if docstring_char in stripped:
                    in_docstring = False
                    after_docstring = i + 1
                continue

            if stripped.startswith("import ") or stripped.startswith("from "):
                last_import_line = i + 1
                continue

            if last_import_line > 0 and stripped and not stripped.startswith("#"):
                break

        return last_import_line, after_docstring

    def _allocate_helper_name(self, file_path: str) -> str:
        """Return a unique helper name for the given canonical file."""

        counter = self._helper_name_counters.get(file_path)
        if counter is None:
            counter = self._discover_helper_counter_seed(file_path)
        helper_name = f"__extracted_func_{counter}"
        self._helper_name_counters[file_path] = counter + 1
        return helper_name

    def _discover_helper_counter_seed(self, file_path: str) -> int:
        """Prime the helper counter based on existing helper names in a file."""

        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except Exception:
            return 0

        pattern = re.compile(r"__extracted_func(?:_(\d+))?")
        max_seen = -1
        for match in pattern.finditer(content):
            suffix = match.group(1)
            idx = int(suffix) if suffix is not None else 0
            if idx > max_seen:
                max_seen = idx
        return max_seen + 1

    def _prepare_extracted_method_signature(
        self,
        fn: ast.FunctionDef,
        method_kind: Literal["instance", "classmethod", "staticmethod"],
        implicit_param: Optional[str],
    ) -> None:
        """Normalize the extracted helper so it behaves like the requested method type."""

        if method_kind == "instance":
            name = implicit_param or "self"
            self._ensure_leading_param(fn, name)
            # Strip any conflicting decorators that might have been synthesized earlier
            self._strip_decorator(fn, "staticmethod")
            self._strip_decorator(fn, "classmethod")
        elif method_kind == "classmethod":
            name = implicit_param or "cls"
            self._ensure_leading_param(fn, name)
            self._strip_decorator(fn, "staticmethod")
            if not self._has_decorator(fn, "classmethod"):
                fn.decorator_list.insert(0, ast.Name(id="classmethod", ctx=ast.Load()))
        elif method_kind == "staticmethod":
            self._strip_decorator(fn, "classmethod")
            if not self._has_decorator(fn, "staticmethod"):
                fn.decorator_list.insert(0, ast.Name(id="staticmethod", ctx=ast.Load()))
        else:
            raise ValueError(f"Unsupported method kind: {method_kind}")

    def _rewrite_call_for_method(
        self,
        node: ast.AST,
        original_name: str,
        new_name: str,
        method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]],
        implicit_param: Optional[str],
        class_name: Optional[str],
    ) -> ast.AST:
        """Rewrite calls to the extracted helper so they use method dispatch semantics."""

        if method_kind is None:
            return node

        rewriter = MethodCallRewriter(
            self._drop_implicit_positional,
            self._drop_implicit_keyword,
            original_name=original_name,
            new_name=new_name,
            method_kind=method_kind,
            implicit_name=implicit_param,
            class_name=class_name,
        )
        return rewriter.visit(node)  # type: ignore[no-any-return]

    @staticmethod
    def _drop_implicit_positional(args: List[ast.expr], implicit_name: str) -> List[ast.expr]:
        """Drop the first positional argument matching ``implicit_name`` if present."""

        result: List[ast.expr] = []
        dropped = False
        for arg in args:
            if not dropped and isinstance(arg, ast.Name) and arg.id == implicit_name:
                dropped = True
                continue
            result.append(arg)
        return result

    @staticmethod
    def _drop_implicit_keyword(
        keywords: List[ast.keyword], implicit_name: str
    ) -> List[ast.keyword]:
        """Drop the first keyword argument whose name matches ``implicit_name``."""

        result: List[ast.keyword] = []
        dropped = False
        for kw in keywords:
            if not dropped and kw.arg == implicit_name:
                dropped = True
                continue
            result.append(kw)
        return result

    def _get_method_context(
        self, func: Optional[FunctionNode], class_name: Optional[str]
    ) -> MethodInfo:
        """Return method metadata for ``func`` when it is defined inside ``class_name``."""

        if func is None or class_name is None:
            return MethodInfo(kind=None, implicit_param=None)

        kind: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
        for decorator in func.decorator_list:
            name = self._decorator_name(decorator)
            if name == "staticmethod":
                kind = "staticmethod"
                break
            if name == "classmethod":
                kind = "classmethod"
                break

        if kind is None:
            kind = "instance"

        implicit_param = None
        if kind in {"instance", "classmethod"}:
            if func.args.args:
                implicit_param = func.args.args[0].arg
            else:
                implicit_param = "self" if kind == "instance" else "cls"

        return MethodInfo(kind=kind, implicit_param=implicit_param)

    @staticmethod
    def _resolve_base_name(expr: ast.expr) -> Optional[str]:
        """Resolve a base-class expression into its dotted name when feasible."""

        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            parts: List[str] = []
            current: ast.expr = expr
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                return ".".join(reversed(parts))
        return None

    @staticmethod
    def _class_info_key(info: ClassInfo) -> Tuple[str, str]:
        """Return a stable identifier for a class definition."""

        return (info.file_path, info.qualname)

    def _find_class_info_by_name(
        self, class_infos: List[ClassInfo], file_path: str, class_name: str
    ) -> Optional[ClassInfo]:
        """Locate class metadata using its defining file and simple name."""

        matches = [
            info for info in class_infos if info.file_path == file_path and info.name == class_name
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        # Prefer the innermost definition (longest qualname) when duplicates exist.
        matches.sort(key=lambda info: info.qualname.count("."), reverse=True)
        return matches[0]

    def _find_class_info_for_base(
        self,
        class_infos: List[ClassInfo],
        base_name: str,
        *,
        prefer_file: Optional[str] = None,
    ) -> Optional[ClassInfo]:
        """Resolve a base-class reference to known class metadata when possible."""

        # Exact qualname match first (covers nested classes written as Outer.Inner)
        qual_matches = [info for info in class_infos if info.qualname == base_name]
        if prefer_file is not None:
            for info in qual_matches:
                if info.file_path == prefer_file:
                    return info
        if qual_matches:
            return qual_matches[0]

        simple_name = base_name.split(".")[-1]
        simple_matches = [info for info in class_infos if info.name == simple_name]
        if prefer_file is not None:
            for info in simple_matches:
                if info.file_path == prefer_file:
                    return info
        if len(simple_matches) == 1:
            return simple_matches[0]
        return None

    def _collect_class_ancestors(
        self, class_info: ClassInfo, class_infos: List[ClassInfo]
    ) -> List[ClassInfo]:
        """Return ancestors starting from the nearest base class."""

        ancestors: List[Tuple[int, ClassInfo]] = []
        visited: Set[Tuple[str, str]] = set()
        queue: deque[Tuple[ClassInfo, int]] = deque([(class_info, 0)])

        while queue:
            current, depth = queue.popleft()
            for base_name in current.bases:
                base_info = self._find_class_info_for_base(
                    class_infos, base_name, prefer_file=current.file_path
                )
                if base_info is None:
                    continue
                key = self._class_info_key(base_info)
                if key in visited:
                    continue
                visited.add(key)
                ancestors.append((depth + 1, base_info))
                queue.append((base_info, depth + 1))

        ancestors.sort(key=lambda item: item[0])
        return [info for _depth, info in ancestors]

    def _find_common_ancestor(
        self,
        class1: Tuple[str, str],
        class2: Tuple[str, str],
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInfo]:
        """Return the nearest shared ancestor class for two class definitions."""

        file1, name1 = class1
        file2, name2 = class2

        info1 = self._find_class_info_by_name(class_infos, file1, name1)
        info2 = self._find_class_info_by_name(class_infos, file2, name2)
        if info1 is None or info2 is None:
            return None

        key1 = self._class_info_key(info1)
        key2 = self._class_info_key(info2)

        chain1 = [info1] + self._collect_class_ancestors(info1, class_infos)
        chain2 = [info2] + self._collect_class_ancestors(info2, class_infos)
        lookup2 = {self._class_info_key(info): info for info in chain2}

        for info in chain1:
            key = self._class_info_key(info)
            if key in lookup2:
                if key == key1 and key == key2:
                    # Identical class; handled elsewhere.
                    continue
                return lookup2[key]
        return None

    def _choose_class_insertion(
        self,
        pair: CodeBlockPair,
        method_info1: MethodInfo,
        method_info2: MethodInfo,
        class_infos: List[ClassInfo],
    ) -> Optional[ClassInsertionPlan]:
        """Determine whether the helper should be inserted into a class context."""
        # Original logic required both method kinds to be equal and non-None. In practice
        # some methods may not have their kind inferred (kind=None) even though they are
        # instance methods (implicit_param present / first arg named 'self'). We relax
        # this by attempting inference when BOTH kinds are None; if only one side is
        # None we keep the conservative skip to avoid mismatches (e.g., staticmethod
        # vs instance).
        k1, k2 = method_info1.kind, method_info2.kind
        inferred_kind: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
        if k1 is None and k2 is None:
            # Infer 'instance' if both have an implicit_param or a first argument name.
            # method_info.implicit_param is populated for recognized methods; as a
            # fallback, treat absence uniformly as instance.
            inferred_kind = "instance"
        else:
            if k1 != k2 or k1 is None or k2 is None:
                return None
        if pair.class1_name is None or pair.class2_name is None:
            return None

        file1 = pair.file_path
        file2 = pair.file_path2 or pair.file_path

        # At this point we know effective_kind is valid because we've already validated k1/k2
        effective_kind: Literal["instance", "classmethod", "staticmethod"] = cast(
            Literal["instance", "classmethod", "staticmethod"], inferred_kind or method_info1.kind
        )
        if file1 == file2 and pair.class1_name == pair.class2_name:
            implicit_param = method_info1.implicit_param or method_info2.implicit_param
            if effective_kind == "instance" and not implicit_param:
                implicit_param = "self"
            if effective_kind == "classmethod" and not implicit_param:
                implicit_param = "cls"
            return ClassInsertionPlan(
                class_name=pair.class1_name,
                file_path=file1,
                method_kind=effective_kind,
                implicit_param=implicit_param,
            )

        ancestor = self._find_common_ancestor(
            (file1, pair.class1_name),
            (file2, pair.class2_name),
            class_infos,
        )
        if ancestor is None:
            return None

        implicit_param = method_info1.implicit_param or method_info2.implicit_param
        if effective_kind == "instance" and not implicit_param:
            implicit_param = "self"
        if effective_kind == "classmethod" and not implicit_param:
            implicit_param = "cls"

        return ClassInsertionPlan(
            class_name=ancestor.name,
            file_path=ancestor.file_path,
            method_kind=effective_kind,
            implicit_param=implicit_param,
        )

    def _evaluate_pairs_serial(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: List[
            Tuple[
                str,
                FunctionNode,
                str,
                ScopeAnalyzer,
                Scope,
                Optional[str],
                Optional[str],
                List[str],
            ]
        ],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: str,
    ) -> List[RefactoringProposal]:
        proposals: List[RefactoringProposal] = []

        progress_mode, tqdm_cls, use_tqdm = self._resolve_progress_backend(progress)
        tqdm_iter = None
        # Always show progress for pair evaluation when progress is enabled, even if verbose=False
        if use_tqdm and tqdm_cls is not None:
            tqdm_iter = tqdm_cls(
                block_pairs,
                total=len(block_pairs),
                desc="unify",
                unit="pair",
                leave=False,
            )

        if use_tqdm and tqdm_iter is not None:
            for pair in tqdm_iter:
                proposal = self._try_refactor_pair_multi_file(pair, all_functions, class_infos)
                if proposal:
                    proposals.append(proposal)
            return proposals

        use_inline_bar = progress_mode in ("auto", "tqdm") and len(block_pairs) > 0 and not use_tqdm
        last_pct = -1
        self._start_inline_status("Analyzing pairs (unify):", use_inline_bar)

        for idx, pair in enumerate(block_pairs, 1):
            proposal = self._try_refactor_pair_multi_file(pair, all_functions, class_infos)
            if proposal:
                proposals.append(proposal)
            if use_inline_bar:
                pct = int(100 * idx / len(block_pairs))
                if pct != last_pct:
                    last_pct = pct
                    self._update_inline_status("Analyzing pairs (unify):", pct)

        self._finish_inline_status(use_inline_bar)

        return proposals

    def _evaluate_pairs_parallel(
        self,
        block_pairs: List[CodeBlockPair],
        all_functions: List[
            Tuple[
                str,
                FunctionNode,
                str,
                ScopeAnalyzer,
                Scope,
                Optional[str],
                Optional[str],
                List[str],
            ]
        ],
        class_infos: List[ClassInfo],
        *,
        verbose: bool,
        progress: str,
    ) -> List[RefactoringProposal]:
        progress_mode, tqdm_cls, tqdm_available = self._resolve_progress_backend(progress)

        pair_count = len(block_pairs)
        cpu_count = os.cpu_count() or 1
        max_workers = min(cpu_count, pair_count)
        if max_workers <= 1:
            return self._evaluate_pairs_serial(
                block_pairs,
                all_functions,
                class_infos,
                verbose=verbose,
                progress=progress,
            )

        engine_config: Dict[str, Optional[object]] = {
            "max_parameters": self.max_parameters,
            "min_lines": self.min_lines,
            "parameterize_constants": self.parameterize_constants,
            "prefer_absolute_imports": self.prefer_absolute_imports,
            "pep420_namespace_packages": self.pep420_namespace_packages,
        }

        tasks = [(idx, pair) for idx, pair in enumerate(block_pairs)]
        ordered_results: List[Optional[RefactoringProposal]] = [None] * pair_count

        use_tqdm = verbose and tqdm_available and tqdm_cls is not None
        tqdm_wrapper = tqdm_cls if use_tqdm else None

        use_inline_bar = verbose and not use_tqdm and progress_mode in ("auto", "tqdm")
        last_pct = -1
        completed = 0
        self._start_inline_status("Analyzing candidate pairs:", use_inline_bar)

        # ProcessPoolExecutor type stubs are restrictive; cast to Any for initializer/initargs
        executor_kwargs: Dict[str, Any] = {
            "max_workers": max_workers,
            "initializer": _initialize_pair_worker,
            "initargs": (engine_config, all_functions, class_infos),
        }
        with ProcessPoolExecutor(**executor_kwargs) as executor:
            futures = [executor.submit(_process_pair_in_worker, task) for task in tasks]
            iterator = as_completed(futures)
            if use_tqdm and tqdm_wrapper is not None:
                iterator = tqdm_wrapper(
                    iterator,
                    total=pair_count,
                    desc="Analyzing candidate pairs",
                    unit="pair",
                    leave=False,
                )

            for future in iterator:
                pair_index, proposal = future.result()
                if proposal:
                    ordered_results[pair_index] = proposal
                completed += 1
                if use_inline_bar:
                    pct = int(100 * completed / pair_count)
                    if pct != last_pct:
                        last_pct = pct
                        self._update_inline_status("Analyzing candidate pairs:", pct)

        self._finish_inline_status(use_inline_bar)

        return [proposal for proposal in ordered_results if proposal is not None]

    @staticmethod
    def _block_line_span(block: Sequence[ast.stmt]) -> Optional[Tuple[int, int]]:
        """Return the (start_line, end_line) span for a contiguous block of statements."""

        if not block:
            return None

        start_node = block[0]
        end_node = block[-1]

        start_line = getattr(start_node, "lineno", None)
        end_line = getattr(end_node, "end_lineno", None) or getattr(end_node, "lineno", None)
        if start_line is None or end_line is None:
            return None

        return int(start_line), int(end_line)

    @staticmethod
    def _body_without_docstring(body: Sequence[ast.stmt]) -> List[ast.stmt]:
        """Return body statements with a leading docstring removed when present."""

        body_list = list(body)
        if not body_list:
            return []

        first_stmt = body_list[0]
        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(first_stmt.value, ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            return body_list[1:]

        return body_list

    def _extract_code_blocks(
        self, function: FunctionNode
    ) -> List[Tuple[Tuple[int, int], List[ast.AST]]]:
        """
        Extract all contiguous code blocks from a function body, including nested bodies.

        Args:
            function: Function definition

        Returns:
            List of (line_range, statements) tuples
        """

        def extract_from_body(body: List[ast.stmt]) -> List[Tuple[Tuple[int, int], List[ast.AST]]]:
            # Extract all contiguous subsequences of minimum length from a given body
            results: List[Tuple[Tuple[int, int], List[ast.AST]]] = []

            # Extract all contiguous subsequences
            for length in range(len(body), 0, -1):
                for start in range(len(body) - length + 1):
                    block = body[start : start + length]

                    # Do not extract blocks that contain nested function/class definitions.
                    # These statements establish new scopes whose bindings must remain in the
                    # original function so later statements can reference them.
                    if any(
                        isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        for stmt in block
                    ):
                        continue

                    span = self._block_line_span(block)
                    if span is None:
                        continue
                    start_line, end_line = span
                    line_count = end_line - start_line + 1

                    if line_count >= self.min_lines:
                        results.append(((start_line, end_line), cast(List[ast.AST], block)))

            # Recurse into nested bodies for control-flow/container statements
            for stmt in body:
                # Skip nested function/class definitions to avoid crossing scopes
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue

                # Common body/orelse containers
                if hasattr(stmt, "body") and isinstance(getattr(stmt, "body"), list):
                    results.extend(extract_from_body(getattr(stmt, "body")))
                if hasattr(stmt, "orelse") and isinstance(getattr(stmt, "orelse"), list):
                    results.extend(extract_from_body(getattr(stmt, "orelse")))

                # With and AsyncWith already covered by .body
                # Try/Except/Finally blocks
                if isinstance(stmt, ast.Try):
                    if stmt.handlers:
                        for h in stmt.handlers:
                            if hasattr(h, "body") and isinstance(h.body, list):
                                results.extend(extract_from_body(h.body))
                    if hasattr(stmt, "finalbody") and isinstance(stmt.finalbody, list):
                        results.extend(extract_from_body(stmt.finalbody))

            return results

        # Prepare top-level body (skip docstring)
        body = self._body_without_docstring(function.body)

        return extract_from_body(body)

    def _has_code_after_block(self, function: ast.FunctionDef, block_end_line: int) -> bool:
        """
        Check if there's any executable code after block_end_line in the function.

        This is used to detect when extracting a block with returns would make
        subsequent code unreachable.

        Args:
            function: Function definition
            block_end_line: End line of the block

        Returns:
            True if there's code after the block
        """
        body = self._body_without_docstring(function.body)

        # Check if any statement starts after block_end_line
        for stmt in body:
            if stmt.lineno > block_end_line:
                return True
        return False

    def _has_returns_in_loops(self, block: List[ast.AST]) -> bool:
        """
        Check if a block contains return statements inside loops.

        Returns in loops are conditional on the loop executing, so if the loop
        doesn't execute (e.g., empty iteration), control continues after the loop.

        Args:
            block: List of AST statements

        Returns:
            True if there are returns inside loop statements
        """

        finder = LoopReturnFinder()
        for stmt in block:
            finder.visit(stmt)
        return finder.has_loop_return

    def _get_used_names(self, node: ast.AST) -> Set[str]:
        """
        Get all variable names that are used (read from) in an AST node.

        This collects all Name nodes with Load context.

        Args:
            node: AST node to analyze

        Returns:
            Set of variable names that are read in the node
        """
        cached = self._used_names_cache.get(node)
        if cached is not None:
            return set(cached)

        collector = NameCollector()
        collector.visit(node)

        frozen = frozenset(collector.used)
        self._used_names_cache[node] = frozen
        return set(frozen)

    def _collect_parameter_names(self, func: FunctionNode) -> Set[str]:
        """Return all argument names for a function (including pos-only and varargs)."""

        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return set()

        params: Set[str] = set()
        args = func.args

        for arg in getattr(args, "posonlyargs", []) or []:
            params.add(arg.arg)
        for arg in args.args:
            params.add(arg.arg)
        for arg in args.kwonlyargs:
            params.add(arg.arg)
        if args.vararg:
            params.add(args.vararg.arg)
        if args.kwarg:
            params.add(args.kwarg.arg)

        return params

    def _deepest_common_ancestry(
        self, anc1: Optional[List[str]], anc2: Optional[List[str]]
    ) -> Optional[str]:
        """Return deepest shared symbol in two ancestry chains (DRY helper \u00a7ref docs/DRY_RUN_2025-11-28.md)."""

        if not anc1 or not anc2:
            return None

        dce: Optional[str] = None
        for left, right in zip(anc1, anc2):
            if left == right:
                dce = left
            else:
                break
        return dce

    def _build_block_binding_snapshot(
        self,
        func: FunctionNode,
        block_nodes: List[ast.AST],
        block_range: Tuple[int, int],
        reassignments: Dict[int, bool],
    ) -> BlockBindingSnapshot:
        """Aggregate binding stats for a block (DRY helper import, see docs/DRY_RUN_2025-11-28.md)."""

        bound_in_block, reassigned_in_block = _collect_block_binding_stats(
            block_nodes, reassignments
        )

        bound_before_block: Set[str] = set()
        block_start_line = block_range[0]
        for stmt in func.body:
            if hasattr(stmt, "lineno") and stmt.lineno < block_start_line:
                stmt_bound: Set[str] = set()
                stmt_reassigned: Set[str] = set()
                _collect_bindings_and_reassignments(
                    stmt, reassignments, stmt_bound, stmt_reassigned
                )
                bound_before_block.update(stmt_bound)

        bound_before_block.update(self._collect_parameter_names(func))

        bound_after_block: Set[str] = set()
        block_end_line = block_range[1]
        for stmt in func.body:
            if hasattr(stmt, "lineno") and stmt.lineno > block_end_line:
                stmt_bound = set()
                stmt_reassigned = set()
                _collect_bindings_and_reassignments(
                    stmt, reassignments, stmt_bound, stmt_reassigned
                )
                bound_after_block.update(stmt_bound)

        initially_bound = bound_in_block - bound_before_block

        return BlockBindingSnapshot(
            bound_in_block=bound_in_block,
            reassigned_in_block=reassigned_in_block,
            bound_before_block=bound_before_block,
            bound_after_block=bound_after_block,
            initially_bound=initially_bound,
        )

    def _find_return_variables(
        self,
        func: FunctionNode,
        block_range: Tuple[int, int],
        initially_bound: Set[str],
        *,
        debug_label: Optional[str] = None,
    ) -> Set[str]:
        """
        Determine which newly-bound variables are read after the block (DRY helper parity).
        """

        if not initially_bound:
            return set()

        block_end_line = block_range[1]
        result: Set[str] = set()
        debug_enabled = bool(os.getenv("DEBUG_VALIDATION"))

        for stmt in func.body:
            if not hasattr(stmt, "lineno") or stmt.lineno <= block_end_line:
                continue

            uses = self._get_used_names(stmt)
            if debug_enabled and debug_label:
                print(
                    f"  {debug_label}: stmt@{stmt.lineno} ({stmt.__class__.__name__}) uses {uses}"
                )

            overlap = uses & initially_bound
            if overlap:
                result.update(overlap)
                if debug_enabled and debug_label:
                    print(
                        f"    RETURN NEEDED ({debug_label}): Variable(s) {overlap} will be returned from extracted function"
                    )

        if debug_enabled and debug_label and result:
            print(f"{debug_label} requires returning: {result}")

        return result

    def _find_block_pairs_multi_file(
        self,
        all_functions: List[
            Tuple[
                str,
                FunctionNode,
                str,
                ScopeAnalyzer,
                Scope,
                Optional[str],
                Optional[str],
                List[str],
            ]
        ],
        *,
        progress: str = "none",
    ) -> List[CodeBlockPair]:
        """
        Find all non-overlapping pairs of code blocks across multiple files.

        Args:
            all_functions: List of (file_path, function, source, scope_analyzer, root_scope)

        Returns:
            List of code block pairs
        """
        pairs = []

        # Progress setup
        use_tqdm = progress in ("tqdm", "auto")
        tqdm_bar = None
        total_funcs = len(all_functions)
        total_func_pairs = (total_funcs * (total_funcs - 1)) // 2 if total_funcs > 1 else 0
        if use_tqdm and total_func_pairs > 0:
            tqdm_cls = self._load_tqdm_wrapper()
            if tqdm_cls is not None:
                tqdm_bar = tqdm_cls(
                    total=total_func_pairs,
                    desc="pairs",
                    unit="fp",
                    dynamic_ncols=True,
                    leave=False,
                )
            else:
                use_tqdm = False

        use_inline = (not use_tqdm) and progress in ("tqdm", "auto") and total_func_pairs > 0
        last_pct = -1
        self._start_inline_status("Pairing blocks:", use_inline)

        func_pairs_done = 0

        # For each pair of functions (including across files)
        for i, entry1 in enumerate(all_functions):
            # Backward compatibility: allow 5-tuples (no class context)
            if len(entry1) >= 8:
                file1, func1, source1, analyzer1, scope1, class1, encl1, anc1 = entry1
            elif len(entry1) == 7:
                file1, func1, source1, analyzer1, scope1, class1, encl1 = entry1
                anc1 = []
            else:
                file1, func1, source1, analyzer1, scope1 = entry1
                class1 = None
                encl1 = None
                anc1 = []

            for entry2 in all_functions[i + 1 :]:
                if len(entry2) >= 8:
                    file2, func2, source2, analyzer2, scope2, class2, encl2, anc2 = entry2
                elif len(entry2) == 7:
                    file2, func2, source2, analyzer2, scope2, class2, encl2 = entry2
                    anc2 = []
                else:
                    file2, func2, source2, analyzer2, scope2 = entry2
                    class2 = None
                    encl2 = None
                    anc2 = []
                # Extract all code blocks from each function
                blocks1 = [
                    (block_range, block_nodes, extract_block_signature(block_nodes))
                    for block_range, block_nodes in self._extract_code_blocks(func1)
                ]
                blocks2 = [
                    (block_range, block_nodes, extract_block_signature(block_nodes))
                    for block_range, block_nodes in self._extract_code_blocks(func2)
                ]

                # Compare all pairs of blocks
                for block1_range, block1_nodes, sig1 in blocks1:
                    for block2_range, block2_nodes, sig2 in blocks2:
                        # Must have same number of statements for unification
                        if len(block1_nodes) != len(block2_nodes):
                            continue

                        # Check minimum size
                        start1, end1 = block1_range
                        start2, end2 = block2_range
                        if (end1 - start1 + 1) < self.min_lines or (
                            end2 - start2 + 1
                        ) < self.min_lines:
                            continue

                        if not quick_filter(sig1, sig2):
                            continue

                        # Create pair with all necessary context
                        pair = CodeBlockPair(
                            file_path=file1,
                            function1_name=func1.name,
                            function2_name=func2.name,
                            block1_range=block1_range,
                            block2_range=block2_range,
                            block1_nodes=block1_nodes,
                            block2_nodes=block2_nodes,
                            file_path2=file2,
                            class1_name=class1,
                            class2_name=class2,
                            enclosing_function1_name=encl1,
                            enclosing_function2_name=encl2,
                            function1_ancestry=anc1,
                            function2_ancestry=anc2,
                            scope_analyzer1=analyzer1,
                            scope_analyzer2=analyzer2,
                            root_scope1=scope1,
                            root_scope2=scope2,
                            source1=source1,
                            source2=source2,
                            function1_node=func1,
                            function2_node=func2,
                        )
                        pairs.append(pair)

                # Update progress per function pair
                func_pairs_done += 1
                if tqdm_bar is not None:
                    try:
                        tqdm_bar.update(1)
                        if func_pairs_done % 20 == 0 or func_pairs_done == total_func_pairs:
                            tqdm_bar.set_postfix({"pairs": len(pairs)}, refresh=True)
                    except Exception:
                        pass
                elif use_inline:
                    pct = int(100 * func_pairs_done / max(total_func_pairs, 1))
                    if pct != last_pct:
                        last_pct = pct
                        self._update_inline_status(
                            "Pairing blocks:",
                            pct,
                            suffix=f"| pairs={len(pairs)}",
                        )
        if tqdm_bar is not None:
            try:
                tqdm_bar.close()
            except Exception:
                pass
        self._finish_inline_status(use_inline)

        return pairs

    def _try_refactor_pair_multi_file(
        self,
        pair: CodeBlockPair,
        all_functions: List[
            Tuple[
                str,
                FunctionNode,
                str,
                ScopeAnalyzer,
                Scope,
                Optional[str],
                Optional[str],
                List[str],
            ]
        ],
        class_infos: List[ClassInfo],
    ) -> Optional[RefactoringProposal]:
        """
        Try to refactor a pair of code blocks using unification (cross-file support).

        Args:
            pair: Code block pair (may be cross-file)
            all_functions: All functions being analyzed
            class_infos: Metadata about classes discovered in analyzed files

        Returns:
            Refactoring proposal or None
        """
        # DEBUG logging
        import os

        if os.getenv("DEBUG_VALIDATION"):
            print("\n=== _try_refactor_pair_multi_file called ===")
            print(f"Functions: {pair.function1_name} and {pair.function2_name}")
            print(f"Block1 range: {pair.block1_range}")
            print(f"Block2 range: {pair.block2_range}")

        # Resolve contextual analyzers and scopes from the aggregated function list
        func1: Optional[FunctionNode] = pair.function1_node
        func2: Optional[FunctionNode] = pair.function2_node
        scope_analyzer1: Optional[ScopeAnalyzer] = None
        scope_analyzer2: Optional[ScopeAnalyzer] = None
        root_scope1: Optional[Scope] = None
        root_scope2: Optional[Scope] = None

        for entry in all_functions:
            (
                file_path,
                func,
                _source,
                analyzer,
                root_scope_entry,
                _class_name,
                _enclosing_func,
                _ancestry,
            ) = entry
            if func1 is None and file_path == pair.file_path and func.name == pair.function1_name:
                func1 = func
                scope_analyzer1 = analyzer
                root_scope1 = root_scope_entry
            if (
                func2 is None
                and file_path == (pair.file_path2 or pair.file_path)
                and func.name == pair.function2_name
            ):
                func2 = func
                scope_analyzer2 = analyzer
                root_scope2 = root_scope_entry
            if func1 is not None and func2 is not None:
                break

        # Fallback to the pair-provided analyzers/scopes when discovery fails
        scope_analyzer = scope_analyzer1 or pair.scope_analyzer1
        root_scope = root_scope1 or pair.root_scope1
        if scope_analyzer2 is None and pair.scope_analyzer2 is not None:
            scope_analyzer2 = pair.scope_analyzer2
        if root_scope2 is None and pair.root_scope2 is not None:
            root_scope2 = pair.root_scope2

        method_info1 = self._get_method_context(func1, pair.class1_name)
        method_info2 = self._get_method_context(func2, pair.class2_name)

        # (Removed specialized full-body extraction fast-path; reverting to generic pairing logic.)

        # DEBUG logging
        import os

        if os.getenv("DEBUG_VALIDATION"):
            print("\n=== Finding Functions ===")
            print(f"Looking for: {pair.function1_name} and {pair.function2_name}")
            print(f"Found func1: {func1 is not None}")
            print(f"Found func2: {func2 is not None}")

        # CRITICAL: Validate that blocks don't contain reassignments without initial bindings
        # Initialize return_variables tracking
        # This will be populated if we find variables that need to be returned
        return_variables_block1: Set[str] = set()
        return_variables_block2: Set[str] = set()

        bound_in_block1: Set[str] = set()
        bound_before_block1: Set[str] = set()
        bound_after_block1: Set[str] = set()
        initially_bound1: Set[str] = set()

        bound_in_block2: Set[str] = set()
        bound_before_block2: Set[str] = set()
        bound_after_block2: Set[str] = set()
        initially_bound2: Set[str] = set()

        # This prevents extracting code like "result = result + 10" when "result = x * 2"
        # is outside the block. Such extractions are fundamentally unsound.
        if func1 and func2:
            # Analyze assignments in both functions
            reassignments1 = self._get_assignment_reuse(func1)
            reassignments2 = self._get_assignment_reuse(func2)

            # Check if block1 contains reassignments without bindings
            has_unsafe1, problematic_vars1 = has_reassignments_without_bindings(
                func1, pair.block1_nodes, reassignments1
            )
            if has_unsafe1:
                self._debug_reject("unsafe_reassignment_block1", pair, str(problematic_vars1))
                return None

            # Check if block2 contains reassignments without bindings
            has_unsafe2, problematic_vars2 = has_reassignments_without_bindings(
                func2, pair.block2_nodes, reassignments2
            )
            if has_unsafe2:
                self._debug_reject("unsafe_reassignment_block2", pair, str(problematic_vars2))
                return None

            # Consolidated helper mirrors DRY's _refactor_engine_helper_2 (docs/DRY_RUN_2025-11-28.md)
            block1_snapshot = self._build_block_binding_snapshot(
                func1, pair.block1_nodes, pair.block1_range, reassignments1
            )
            block2_snapshot = self._build_block_binding_snapshot(
                func2, pair.block2_nodes, pair.block2_range, reassignments2
            )

            bound_in_block1 = block1_snapshot.bound_in_block
            bound_before_block1 = block1_snapshot.bound_before_block
            bound_after_block1 = block1_snapshot.bound_after_block
            initially_bound1 = block1_snapshot.initially_bound

            bound_in_block2 = block2_snapshot.bound_in_block
            bound_before_block2 = block2_snapshot.bound_before_block
            bound_after_block2 = block2_snapshot.bound_after_block
            initially_bound2 = block2_snapshot.initially_bound

            debug_enabled = bool(os.getenv("DEBUG_VALIDATION"))
            if debug_enabled:
                print("\n=== Block1 Validation Debug ===")
                print(f"Function: {pair.function1_name}")
                print(f"Block lines: {pair.block1_range}")
                print(f"Bound in block: {bound_in_block1}")
                print(f"Bound before block: {bound_before_block1}")
                print(f"Newly bound in block: {initially_bound1}")

            return_variables_block1 = self._find_return_variables(
                func1,
                pair.block1_range,
                initially_bound1,
                debug_label="Block1 Validation Debug" if debug_enabled else None,
            )

            if debug_enabled:
                print("\n=== Block2 Validation Debug ===")
                print(f"Function: {pair.function2_name}")
                print(f"Block lines: {pair.block2_range}")
                print(f"Bound in block: {bound_in_block2}")
                print(f"Bound before block: {bound_before_block2}")
                print(f"Newly bound in block: {initially_bound2}")

            return_variables_block2 = self._find_return_variables(
                func2,
                pair.block2_range,
                initially_bound2,
                debug_label="Block2 Validation Debug" if debug_enabled else None,
            )

        # Check if both blocks are value-producing or both are not
        # Blocks with return_variables are treated as value-producing because
        # we will add return statements for those variables
        value_prod1 = is_value_producing(cast(List[ast.stmt], pair.block1_nodes)) or bool(
            return_variables_block1
        )
        value_prod2 = is_value_producing(cast(List[ast.stmt], pair.block2_nodes)) or bool(
            return_variables_block2
        )

        if os.getenv("DEBUG_VALIDATION"):
            print(f"  Value-producing check: block1={value_prod1}, block2={value_prod2}")
            if return_variables_block1:
                print(f"  Block1 has return_variables: {return_variables_block1}")
            if return_variables_block2:
                print(f"  Block2 has return_variables: {return_variables_block2}")

        if value_prod1 != value_prod2:
            if os.getenv("DEBUG_VALIDATION"):
                print("  REJECTED: Value-producing mismatch")
            self._debug_reject("value_producing_mismatch", pair)
            return None

        # CRITICAL: If blocks are NATURALLY value-producing (have return statements),
        # ensure complete return coverage. This prevents extracting partial control flow.
        # Skip this check for blocks that will have return statements ADDED for return_variables
        if value_prod1 and not return_variables_block1:  # Naturally value-producing
            from .extractor import has_complete_return_coverage

            if not has_complete_return_coverage(cast(List[ast.stmt], pair.block1_nodes)):
                if os.getenv("DEBUG_VALIDATION"):
                    print("  REJECTED: Block1 missing complete return coverage")
                self._debug_reject("incomplete_return_coverage_block1", pair)
                return None
            if not has_complete_return_coverage(cast(List[ast.stmt], pair.block2_nodes)):
                if os.getenv("DEBUG_VALIDATION"):
                    print("  REJECTED: Block2 missing complete return coverage")
                self._debug_reject("incomplete_return_coverage_block2", pair)
                return None

        # Heuristic: avoid extracting trivial single-line return blocks that just
        # return a previously bound local name (e.g., `return result`). Prefer
        # extracting the preceding computation that produces the value.
        def _is_trivial_return_of_bound_name(
            block_nodes: List[ast.AST], bound_before_block: Set[str], bound_in_block: Set[str]
        ) -> bool:
            if len(block_nodes) != 1:
                return False
            stmt = block_nodes[0]
            return (
                isinstance(stmt, ast.Return)
                and isinstance(stmt.value, ast.Name)
                and stmt.value.id in bound_before_block
                and stmt.value.id not in bound_in_block
            )

        if _is_trivial_return_of_bound_name(
            pair.block1_nodes, bound_before_block1, bound_in_block1
        ) and _is_trivial_return_of_bound_name(
            pair.block2_nodes, bound_before_block2, bound_in_block2
        ):
            if os.getenv("DEBUG_VALIDATION"):
                print(
                    "  REJECTED: Trivial single-line return blocks (prefer extracting computation)"
                )
            self._debug_reject("trivial_return_blocks", pair)
            return None

        # Check structural similarity
        if not self._are_structurally_similar(pair.block1_nodes, pair.block2_nodes):
            if os.getenv("DEBUG_VALIDATION"):
                print("  REJECTED: Not structurally similar")
            self._debug_reject("not_structurally_similar", pair)
            return None

        # Attempt unification
        blocks = [pair.block1_nodes, pair.block2_nodes]
        hygienic_renames: List[Dict[str, str]] = [{}, {}]

        if os.getenv("DEBUG_VALIDATION"):
            print("  Attempting unification...")

        try:
            substitution = self.unifier.unify_blocks(blocks, hygienic_renames)
        except Exception as e:
            if os.getenv("DEBUG_VALIDATION"):
                print(f"  REJECTED: Unification exception: {e}")
            self._debug_reject("unification_exception", pair, str(e))
            return None

        if not substitution:
            if os.getenv("DEBUG_VALIDATION"):
                print("  REJECTED: Unification failed (no substitution)")
            self._debug_reject("unification_failed", pair)
            return None

        if os.getenv("DEBUG_VALIDATION"):
            print("  ✓ Unification successful")
            print(f"  Substitution: {substitution}")

        # Pre-compute the deepest common enclosing function (for same-file cases)
        dce_insert_func: Optional[str] = None
        try:
            same_file_ctx = pair.file_path2 is not None and pair.file_path2 == pair.file_path
            if same_file_ctx:
                dce_insert_func = self._deepest_common_ancestry(
                    pair.function1_ancestry, pair.function2_ancestry
                )
        except Exception:
            dce_insert_func = None

        # Get enclosing names to avoid shadowing (module-level by default)
        enclosing_names = set(root_scope.bindings.keys()) if root_scope else set()
        # If we plan to insert into a specific function scope (DCE), enrich hygiene set with that
        # function's local bindings to avoid name collisions
        if dce_insert_func:
            try:
                for fpath, fn, _src, analyzer, rscope, _cls, _encl, _anc in all_functions:
                    if fpath == (pair.file_path2 or pair.file_path) and fn.name == dce_insert_func:
                        func_scope = analyzer.node_scopes.get(fn)
                        if func_scope is not None:
                            enclosing_names.update(func_scope.bindings.keys())
                        break
            except Exception:
                # Best effort only
                pass
        # Hygiene improvement: if we'll insert into a specific function scope (DCE),
        # include that function's local bindings to avoid name collisions.
        try:
            same_file_for_hygiene = (
                pair.file_path2 is not None and pair.file_path2 == pair.file_path
            )

            target_insert_fn: Optional[str] = None
            if same_file_for_hygiene:
                target_insert_fn = self._deepest_common_ancestry(
                    pair.function1_ancestry, pair.function2_ancestry
                )
            if target_insert_fn:
                # Locate the target function node and its scope analyzer for this file
                target_func_node = None
                target_analyzer: Optional[ScopeAnalyzer] = None
                for fpath, fn, _src, analyzer, _rscope, _cls, _encl, _anc in all_functions:
                    if fpath == pair.file_path and fn.name == target_insert_fn:
                        target_func_node = fn
                        target_analyzer = analyzer
                        break
                if target_func_node is not None and target_analyzer is not None:
                    func_scope = target_analyzer.node_scopes.get(target_func_node)
                    if func_scope is not None:
                        enclosing_names.update(func_scope.bindings.keys())
        except Exception:
            # Best-effort only; if anything goes wrong, proceed with module-level names
            pass

        # Compute free variables for both blocks (variables used but not defined in each block)
        # Use block1's free variables to derive parameters for the extracted function,
        # but validate incomplete lifetimes independently for each block.
        free_vars1 = (
            scope_analyzer.get_free_variables(pair.block1_nodes) if scope_analyzer else set()
        )
        free_vars2 = (
            scope_analyzer2.get_free_variables(pair.block2_nodes) if scope_analyzer2 else set()
        )

        # CRITICAL VALIDATION: Reject proposals with incomplete variable lifetimes
        # A free variable bound AFTER the block is problematic - we'd be using it before it's defined.
        # However, free variables bound BEFORE the block are OK - they become parameters.
        if free_vars1 & bound_after_block1:
            incomplete_vars = free_vars1 & bound_after_block1
            if os.getenv("DEBUG_VALIDATION"):
                print(
                    f"  REJECTED: Block1 uses variables defined AFTER the block: {incomplete_vars}"
                )
                print("    These variables would be used before they're defined")
            self._debug_reject("incomplete_lifetime_block1", pair, str(incomplete_vars))
            return None

        if free_vars2 & bound_after_block2:
            incomplete_vars = free_vars2 & bound_after_block2
            if os.getenv("DEBUG_VALIDATION"):
                print(
                    f"  REJECTED: Block2 uses variables defined AFTER the block: {incomplete_vars}"
                )
            self._debug_reject("incomplete_lifetime_block2", pair, str(incomplete_vars))
            return None

        # Find all variables used in augmented assignments in block1
        # These variables MUST be passed as parameters even if they appear in substitution
        # because augmented assignments (total += x) READ the variable before writing it
        aug_finder = AugAssignFinder()
        for node in pair.block1_nodes:
            aug_finder.visit(node)
        aug_assign_vars = aug_finder.aug_assign_targets

        # Handle augmented assignment variables specially
        # 1) Remove them from substitution so they remain as free variables/parameters
        # 2) Record the name mapping per block for call generation later
        aug_assign_param_mappings: Dict[str, Dict[int, str]] = {}
        params_to_remove = []
        for param_name, exprs in list(substitution.param_expressions.items()):
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.Name) and expr.id in aug_assign_vars:
                    if param_name not in aug_assign_param_mappings:
                        aug_assign_param_mappings[param_name] = {}
                    aug_assign_param_mappings[param_name][block_idx] = expr.id
            if 0 in aug_assign_param_mappings.get(param_name, {}):
                params_to_remove.append(param_name)

        # Store the mappings in the substitution object for later use (dynamic attribute)
        if not hasattr(substitution, "aug_assign_mappings"):
            setattr(substitution, "aug_assign_mappings", {})
        aug_mappings = cast(Dict[str, Dict[int, str]], getattr(substitution, "aug_assign_mappings"))
        for param_name, block_mappings in aug_assign_param_mappings.items():
            if 0 in block_mappings:
                block1_var_name = block_mappings[0]
                aug_mappings[block1_var_name] = block_mappings

        # Remove the augmented assignment parameters from substitution
        for param_name in params_to_remove:
            del substitution.param_expressions[param_name]

        # Never parameterize entire f-strings: if any parameter maps to a JoinedStr in
        # the template block (block 0), remove it so the f-string structure is preserved
        fstring_params = []
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.JoinedStr):
                    fstring_params.append(param_name)
                    break
        for param_name in fstring_params:
            del substitution.param_expressions[param_name]

        # Remove variables that have been parameterized from free_vars
        # If a variable was parameterized (e.g., 'user' -> '__param_5'),
        # it's no longer free - it's been replaced by a parameter
        # EXCEPT: variables in augmented assignments MUST remain free variables
        parameterized_vars = set()
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                # Only check the first block (template block)
                if block_idx == 0 and isinstance(expr, ast.Name):
                    # Don't add to parameterized_vars if it's an augmented assignment target
                    if expr.id not in aug_assign_vars:
                        parameterized_vars.add(expr.id)

        # Initialize working free_vars from block1's perspective (template block)
        free_vars = set(free_vars1) - parameterized_vars

        # CRITICAL: Check if any free variables are declared global or nonlocal
        # If a free variable is global/nonlocal, we cannot parameterize it
        # because you cannot have a parameter that is also declared global/nonlocal
        # This would create: SyntaxError: name 'x' is parameter and global
        assert scope_analyzer is not None
        func1_scope_id = None
        for node, scope in scope_analyzer.node_scopes.items():
            if isinstance(node, ast.FunctionDef) and node.name == pair.function1_name:
                func1_scope_id = scope.scope_id
                break

        globals_to_declare_in_extracted: Set[str] = set()
        nonlocals_to_declare_in_extracted: Set[str] = set()

        if func1_scope_id is not None:
            # Check if any variables relevant to this block are global or nonlocal in this scope
            global_vars = scope_analyzer.global_vars.get(func1_scope_id, set())
            nonlocal_vars = scope_analyzer.nonlocal_vars.get(func1_scope_id, set())

            # For parameterization safety: if a free variable is declared global/nonlocal, do not parameterize it
            problematic = free_vars & (global_vars | nonlocal_vars)

            # Collect assignment targets and explicit decls inside the template blocks
            v = AssignTargetVisitor()
            for n in pair.block1_nodes:
                v.visit(n)
            for n in pair.block2_nodes:
                v.visit(n)
            assigned_names = v.assigned_names
            declared_global_in_block = v.declared_global_in_block
            declared_nonlocal_in_block = v.declared_nonlocal_in_block

            # Names that are assigned within the block and are global/nonlocal in the enclosing function
            # MUST be declared in the extracted helper to preserve assignment semantics,
            # even if they are not free variables of the original block.
            assigned_problematic_any = assigned_names & (global_vars | nonlocal_vars)

            # For assigned globals/nonlocals that weren't explicitly declared within the block,
            # promote the declaration into the extracted function body.
            globals_to_declare_in_extracted = (
                assigned_problematic_any & global_vars
            ) - declared_global_in_block
            nonlocals_to_declare_in_extracted = (
                assigned_problematic_any & nonlocal_vars
            ) - declared_nonlocal_in_block

            # Do not parameterize free variables that are global/nonlocal; let them remain free
            # so the extracted function references the outer binding.
            free_vars -= problematic

        # Extract function
        try:
            func_def, param_order = self.extractor.extract_function(
                template_block=pair.block1_nodes,
                substitution=substitution,
                free_variables=free_vars,
                enclosing_names=enclosing_names,
                is_value_producing=value_prod1,
                return_variables=list(return_variables_block1),
                global_decls=(
                    globals_to_declare_in_extracted if globals_to_declare_in_extracted else None
                ),
                nonlocal_decls=(
                    nonlocals_to_declare_in_extracted if nonlocals_to_declare_in_extracted else None
                ),
                # Use hygienic double-underscore name; engine will prefix underscore for methods.
                function_name="__extracted_func",
            )
        except Exception:
            return None

        # Check for orphaned variables before proceeding
        # Need to find the function nodes to check for orphans
        func1_for_orphans: Optional[FunctionNode] = None
        func2_for_orphans: Optional[FunctionNode] = None
        for entry in all_functions:
            file_path, func = entry[0], entry[1]
            if file_path == pair.file_path and func.name == pair.function1_name:
                func1_for_orphans = func
            if (
                file_path == (pair.file_path2 or pair.file_path)
                and func.name == pair.function2_name
            ):
                func2_for_orphans = func

        if func1_for_orphans and func2_for_orphans:
            # Get block indices within their respective function bodies
            indices1 = self._get_block_indices(func1_for_orphans, pair.block1_nodes)
            indices2 = self._get_block_indices(func2_for_orphans, pair.block2_nodes)

            if indices1 and indices2:
                # Get function bodies (skip docstring)
                body1 = func1_for_orphans.body
                start_idx1 = 0
                if (
                    body1
                    and isinstance(body1[0], ast.Expr)
                    and isinstance(body1[0].value, ast.Constant)
                    and isinstance(body1[0].value.value, str)
                ):
                    start_idx1 = 1
                body1 = body1[start_idx1:]

                body2 = func2_for_orphans.body
                start_idx2 = 0
                if (
                    body2
                    and isinstance(body2[0], ast.Expr)
                    and isinstance(body2[0].value, ast.Constant)
                    and isinstance(body2[0].value.value, str)
                ):
                    start_idx2 = 1
                body2 = body2[start_idx2:]

                # Check for orphaned variables in both blocks
                has_orphans1, orphans1 = has_orphaned_variables(
                    cast(List[ast.AST], body1), indices1
                )
                has_orphans2, orphans2 = has_orphaned_variables(
                    cast(List[ast.AST], body2), indices2
                )

                if has_orphans1 or has_orphans2:
                    # Cannot extract - would create orphaned variable references
                    self._debug_reject("orphaned_variables", pair)
                    return None

        # Generate replacement calls
        replacements: List[Replacement] = []

        # Map block indices to their return variables
        return_vars_by_block = {0: list(return_variables_block1), 1: list(return_variables_block2)}

        for block_idx, (block_range, file_path) in enumerate(
            [
                (pair.block1_range, pair.file_path),
                (pair.block2_range, pair.file_path2 or pair.file_path),
            ]
        ):
            try:
                call_node = self.extractor.generate_call(
                    function_name=func_def.name,
                    block_idx=block_idx,
                    substitution=substitution,
                    param_order=param_order,
                    free_variables=free_vars,
                    is_value_producing=value_prod1,
                    return_variables=return_vars_by_block[block_idx],
                    hygienic_renames=hygienic_renames,
                )
                # Guard-rail: validate that the generated call does not reference
                # undefined names at the call site. This rejects brittle proposals
                # that leak placeholders (e.g., "__param_1") or invented locals
                # like "filtered"/"mapped" that are not bound before the block.
                # Allowed names = variables bound before the block ∪ free variables
                # (builtins are implicitly allowed by runtime).
                allowed_before: Set[str]
                if block_idx == 0:
                    allowed_before = set(bound_before_block1) | set(free_vars1)
                else:
                    allowed_before = set(bound_before_block2) | set(free_vars2)

                used_in_call = self._get_used_names(call_node)
                # Whitelist a small set of common builtins used in arguments
                builtin_whitelist = {
                    "len",
                    "sum",
                    "min",
                    "max",
                    "any",
                    "all",
                    "map",
                    "filter",
                    "sorted",
                    "list",
                    "dict",
                    "set",
                    "range",
                    "int",
                    "float",
                    "str",
                    "bool",
                    "enumerate",
                    "zip",
                }
                invalid_names = set()
                for name in used_in_call:
                    if name == func_def.name:
                        continue  # referring to the helper itself is handled elsewhere
                    if name.startswith("__param_"):
                        invalid_names.add(name)
                        continue
                    if name in allowed_before or name in builtin_whitelist:
                        continue
                    invalid_names.add(name)

                if invalid_names:
                    # Reject this proposal as it would introduce undefined names
                    self._debug_reject(
                        "undefined_names_in_call",
                        pair,
                        detail=f"block{block_idx+1}: {sorted(invalid_names)}",
                    )
                    return None
                # Store file_path and class context
                class_name = pair.class1_name if block_idx == 0 else pair.class2_name
                method_info = method_info1 if block_idx == 0 else method_info2
                replacements.append(
                    Replacement(
                        line_range=block_range,
                        node=call_node,
                        file_path=file_path,
                        class_name=class_name,
                        method_kind=method_info.kind,
                        implicit_param=method_info.implicit_param,
                    )
                )
            except Exception:
                return None

        # Multi-occurrence clustering (same-file): look for additional identical blocks
        # beyond the initial pair and include them in this proposal as extra replacements.
        # This helps cases like example1_simple where three functions share the same
        # validator block; by default pairwise selection would only cover two.
        try:
            from .block_signature import extract_block_signature, quick_filter as _qf

            # Only attempt simple clustering for module-level, non-returning validators
            same_file_ctx = (pair.file_path2 is None) or (pair.file_path2 == pair.file_path)
            if same_file_ctx and not return_variables_block1 and not return_variables_block2:
                # Build a set of already covered ranges to avoid duplicates
                covered = {
                    (pair.file_path, pair.block1_range),
                    (pair.file_path2 or pair.file_path, pair.block2_range),
                }
                # Template signature from block1
                tmpl_sig = extract_block_signature(pair.block1_nodes)

                # Gather candidates from same file functions
                for entry in all_functions:
                    fpath, fn, _src, analyzerX, _rscopeX, clsX, _enclX, _ancX = (
                        entry if len(entry) >= 8 else (*entry, None, None, None)
                    )
                    if fpath != pair.file_path:
                        continue
                    # Skip the original two functions
                    if fn.name in (pair.function1_name, pair.function2_name):
                        # Still scan, but avoid ranges we've already taken
                        pass
                    # Extract blocks and test quick filter against template
                    for cand_range, cand_nodes in self._extract_code_blocks(fn):
                        if (fpath, cand_range) in covered:
                            continue
                        # Minimum size gate
                        start_line, end_line = cand_range
                        if (end_line - start_line + 1) < self.min_lines:
                            continue
                        cand_sig = extract_block_signature(cand_nodes)
                        if not _qf(tmpl_sig, cand_sig):
                            continue
                        # Try to unify template block with candidate
                        try:
                            subst2 = self.unifier.unify_blocks(
                                [pair.block1_nodes, cand_nodes], [{}, {}]
                            )
                        except Exception:
                            continue
                        if not subst2:
                            continue
                        # Orphan check for candidate within its function body
                        indices = self._get_block_indices(fn, cand_nodes)
                        if indices is None:
                            continue
                        # Skip docstring in body
                        body = self._body_without_docstring(fn.body)
                        has_orph, _orph = has_orphaned_variables(cast(List[ast.AST], body), indices)
                        if has_orph:
                            continue
                        # Generate a call node for the candidate
                        try:
                            call_node2 = self.extractor.generate_call(
                                function_name=func_def.name,
                                block_idx=1,
                                substitution=subst2,
                                param_order=param_order,
                                free_variables=free_vars,
                                is_value_producing=value_prod1,
                                return_variables=[],
                                hygienic_renames=[{}, {}],
                            )
                        except Exception:
                            continue
                        # Validate candidate call-site does not reference undefined names
                        try:
                            used2 = self._get_used_names(call_node2)
                            if any(n.startswith("__param_") for n in used2):
                                # Skip brittle candidate that leaked placeholders
                                continue
                            # Compute names bound before candidate block and free vars within it
                            try:
                                from .assignment_analyzer import (
                                    _collect_bindings_and_reassignments as _cbar,
                                )
                            except Exception:
                                _cbar = None  # type: ignore

                            bound_before_cand: Set[str] = set()
                            if _cbar is not None:
                                reassignX = self._get_assignment_reuse(fn)
                                start_line_cand = cand_range[0]
                                for stmt in fn.body:
                                    if hasattr(stmt, "lineno") and stmt.lineno < start_line_cand:
                                        bset: Set[str] = set()
                                        rset: Set[str] = set()
                                        _cbar(stmt, reassignX, bset, rset)
                                        bound_before_cand.update(bset)
                                # Treat function params as bound
                                if isinstance(fn, ast.FunctionDef):
                                    param_namesX: Set[str] = set()
                                    for arg in fn.args.args:
                                        param_namesX.add(arg.arg)
                                    for arg in getattr(fn.args, "posonlyargs", []) or []:
                                        param_namesX.add(arg.arg)
                                    for arg in fn.args.kwonlyargs:
                                        param_namesX.add(arg.arg)
                                    if fn.args.vararg:
                                        param_namesX.add(fn.args.vararg.arg)
                                    if fn.args.kwarg:
                                        param_namesX.add(fn.args.kwarg.arg)
                                    bound_before_cand.update(param_namesX)
                            free_vars_cand: Set[str] = set()
                            if analyzerX is not None:
                                try:
                                    free_vars_cand = set(analyzerX.get_free_variables(cand_nodes))
                                except Exception:
                                    free_vars_cand = set()
                            allowed_cand = bound_before_cand | free_vars_cand
                            builtin_whitelist = {
                                "len",
                                "sum",
                                "min",
                                "max",
                                "any",
                                "all",
                                "map",
                                "filter",
                                "sorted",
                                "list",
                                "dict",
                                "set",
                                "range",
                                "int",
                                "float",
                                "str",
                                "bool",
                                "enumerate",
                                "zip",
                            }
                            invalid2 = set()
                            for name in used2:
                                if name == func_def.name:
                                    continue
                                if name in allowed_cand or name in builtin_whitelist:
                                    continue
                                invalid2.add(name)
                            if invalid2:
                                # Skip this candidate replacement
                                continue
                        except Exception:
                            # On any validator error, be conservative and skip candidate
                            continue
                        # Append replacement
                        replacements.append(
                            Replacement(
                                line_range=cand_range,
                                node=call_node2,
                                file_path=fpath,
                                class_name=clsX,
                                method_kind=None,
                                implicit_param=None,
                            )
                        )
                        covered.add((fpath, cand_range))
        except Exception:
            # Non-fatal: clustering is best-effort only
            pass
        # Determine canonical file for extracted function
        # Default to the first file, but this may change if we insert into an ancestor class
        canonical_file = pair.file_path

        # Create proposal
        is_cross_file = pair.file_path2 is not None and pair.file_path != pair.file_path2

        desc = f"Extract common code from {pair.function1_name}"
        if is_cross_file:
            assert pair.file_path2 is not None
            desc += f" ({Path(pair.file_path).name}) and {pair.function2_name} ({Path(pair.file_path2).name})"
        else:
            desc += f" and {pair.function2_name}"

        # Default to module-level insertion; when possible insert into deepest common enclosing function.
        insert_into_class = None
        insert_into_function = None
        method_kind_metadata: Optional[Literal["instance", "classmethod", "staticmethod"]] = None
        method_param_name: Optional[str] = None

        # Consider pairs from the same file even if file_path2 is None (same-file pairing)
        same_file = (pair.file_path2 is None) or (pair.file_path2 == pair.file_path)

        # Prefer insertion into the function that actually contains BOTH blocks when they come from the
        # same function (process_user_data & process_admin_data are top-level siblings: no shared ancestry).
        # If the two functions are the SAME name (duplicates), insert into that function instead of module.
        if same_file:
            # Only consider deepest common enclosing function based on ancestry; do NOT use
            # simple name equality as methods across different classes may share the same
            # name but are not in the same function scope. Name-equality caused incorrectly
            # inserting helpers inside one sibling method.
            if pair.function1_ancestry is not None and pair.function2_ancestry is not None:
                dce = self._deepest_common_ancestry(
                    pair.function1_ancestry, pair.function2_ancestry
                )
                if dce:
                    insert_into_function = dce

        class_plan: Optional[ClassInsertionPlan] = None
        if insert_into_function is None:
            class_plan = self._choose_class_insertion(
                pair,
                method_info1,
                method_info2,
                class_infos,
            )

        if class_plan is not None:
            # Refined insertion policy:
            # 1. If both blocks are from the SAME class -> insert into that class.
            # 2. If blocks are from DIFFERENT classes and class_plan points to a COMMON ANCESTOR
            #    that is neither of the concrete classes, insert into ancestor (shared visibility).
            # 3. If class_plan resolves to one of the concrete classes while the other differs ->
            #    fallback to module-level to preserve accessibility (avoid privileging one sibling).
            same_class = pair.class1_name is not None and pair.class1_name == pair.class2_name
            target_is_concrete_sibling = (
                class_plan.class_name in {pair.class1_name, pair.class2_name} and not same_class
            )
            if target_is_concrete_sibling:
                # Helper would become invisible to the other sibling; abort class insertion.
                class_plan = None
            else:
                insert_into_class = class_plan.class_name
                canonical_file = class_plan.file_path
                method_kind_metadata = class_plan.method_kind
                method_param_name = class_plan.implicit_param

        # SAFETY: Avoid refactoring across closures with nonlocal variables for now.
        # If the containing functions (func1/func2) declare any nonlocal variables, skip this proposal
        # to preserve known semantics and baseline expectations (e.g., closure_adversarial.py).
        try:
            nonlocal_in_func1 = False
            nonlocal_in_func2 = False
            if scope_analyzer1 and func1 is not None:
                scope_id1 = scope_analyzer1.node_scopes.get(func1)
                if scope_id1:
                    nlv1 = scope_analyzer1.nonlocal_vars.get(scope_id1.scope_id, set())
                    nonlocal_in_func1 = bool(nlv1)
            elif func1 is not None:
                nonlocal_in_func1 = self._function_contains_nonlocal(func1)

            if scope_analyzer2 and func2 is not None:
                scope_id2 = scope_analyzer2.node_scopes.get(func2)
                if scope_id2:
                    nlv2 = scope_analyzer2.nonlocal_vars.get(scope_id2.scope_id, set())
                    nonlocal_in_func2 = bool(nlv2)
            elif func2 is not None:
                nonlocal_in_func2 = self._function_contains_nonlocal(func2)

            if nonlocal_in_func1 or nonlocal_in_func2:
                self._debug_reject("nonlocal_safety_skip", pair)
                return None
        except Exception:
            # On any analyzer lookup issue, be conservative and proceed without special handling
            pass

        proposal = RefactoringProposal(
            file_path=canonical_file,
            extracted_function=func_def,
            replacements=replacements,  # Now includes file_path
            description=desc,
            parameters_count=len(substitution.param_expressions),
            return_variables=list(return_variables_block1),
            insert_into_class=insert_into_class,
            insert_into_function=insert_into_function,
            method_kind=method_kind_metadata,
            method_param_name=method_param_name,
        )

        return proposal

    def apply_refactoring(self, file_path: str, proposal: RefactoringProposal) -> str:
        """
        Apply a refactoring proposal to a file.

        Args:
            file_path: Path to file
            proposal: Refactoring proposal

        Returns:
            Modified source code
        """
        # All proposals now use multi-file format
        modified_files = self.apply_refactoring_multi_file(proposal)
        # Backward-compat: handle tuple return (modified_files, changed_paths)
        if isinstance(modified_files, tuple):
            modified_files = modified_files[0]
        # Return the content for the requested file
        return modified_files.get(file_path, "")

    def apply_refactoring_multi_file(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """
        Apply a cross-file refactoring proposal.

        Args:
            proposal: Refactoring proposal

        Returns:
            Dict mapping file paths to modified source code
        """
        # Group replacements by file
        replacements_by_file: Dict[str, List[Replacement]] = {}
        for repl in proposal.replacements:
            file_path = repl.file_path or proposal.file_path
            replacements_by_file.setdefault(file_path, []).append(repl)

        # Ensure canonical file is always processed so extracted helper is emitted
        replacements_by_file.setdefault(proposal.file_path, [])

        # Determine helper names ONCE to avoid mismatches across files
        # Capture the original helper name before any renaming, and compute the final name
        original_helper_name = proposal.extracted_function.name
        if original_helper_name == "__extracted_func":
            proposal.extracted_function.name = self._allocate_helper_name(proposal.file_path)
        elif proposal.insert_into_class and not original_helper_name.startswith("_"):
            # Preserve user-provided helper names but keep them non-public inside classes
            proposal.extracted_function.name = f"_{original_helper_name}"
        final_func_name = proposal.extracted_function.name

        # Process each file
        modified_files: Dict[str, str] = {}

        for file_path, replacements in replacements_by_file.items():
            # Read original source
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Sort replacements by line number (reverse order)
            replacements = sorted(replacements, key=lambda r: r.line_range[0], reverse=True)

            # Apply each replacement (reverse sorted prevents earlier line shifts)
            for repl in replacements:
                start_line, end_line = repl.line_range
                replacement_node = copy.deepcopy(repl.node)
                class_name = repl.class_name

                # Validate line numbers
                if start_line < 1 or start_line > len(lines):
                    print(
                        f"Warning: Invalid line range {start_line}-{end_line} for {file_path} (file has {len(lines)} lines)"
                    )
                    print("Skipping this replacement")
                    continue
                if end_line > len(lines):
                    print(
                        f"Warning: End line {end_line} exceeds file length {len(lines)} for {file_path}"
                    )
                    print("Adjusting to end of file")
                    end_line = len(lines)

                # Rewrite call sites for method extraction
                if proposal.insert_into_class and class_name:
                    replacement_node = self._rewrite_call_for_method(
                        replacement_node,
                        original_helper_name,
                        final_func_name,
                        repl.method_kind or proposal.method_kind,
                        repl.implicit_param or proposal.method_param_name,
                        class_name,
                    )
                elif proposal.insert_into_function:
                    # Rewrite to local function call (no attribute), but ensure call uses final_func_name
                    # Simple textual AST rewrite: replace original helper name with final
                    if original_helper_name != final_func_name:
                        replacement_node = self._retarget_helper_calls(
                            replacement_node,
                            original_helper_name,
                            final_func_name,
                        )
                else:
                    # Module-level insertion:
                    # If the helper was renamed (e.g., from '__extracted_func' to 'extracted_func' or custom),
                    # rewrite call sites accordingly.
                    if original_helper_name != final_func_name:
                        replacement_node = self._retarget_helper_calls(
                            replacement_node,
                            original_helper_name,
                            final_func_name,
                        )

                replacement_code = ast.unparse(replacement_node)
                code_lines = replacement_code.split("\n")
                indent = self._get_indent(lines[start_line - 1])

                # Build indented replacement block: indent ALL non-empty lines consistently
                replacement_lines: List[str] = []
                for line in code_lines:
                    if line.strip():
                        replacement_lines.append(indent + line + "\n")
                    else:
                        replacement_lines.append("\n")

                # Splice into source
                lines[start_line - 1 : end_line] = replacement_lines

            # Insert helper into canonical file or import into others
            if file_path == proposal.file_path:
                if proposal.insert_into_function:
                    fn_insert_info = self._find_function_insert_position_before_body_statements(
                        "".join(lines), proposal.insert_into_function
                    )
                    fn_code = ast.unparse(proposal.extracted_function)
                    fn_lines = [l + "\n" for l in fn_code.split("\n")]
                    if fn_insert_info is None:
                        insert_line = self._find_insert_position(lines)
                        lines[insert_line:insert_line] = fn_lines + ["\n", "\n"]
                    else:
                        insert_at_zero_based, indent = fn_insert_info
                        inner_indent = indent + "    "
                        indented: List[str] = []
                        for line in fn_lines:
                            if line.strip():
                                indented.append(inner_indent + line)
                            else:
                                indented.append(line)
                        prefix: List[str] = []
                        if insert_at_zero_based > 0 and lines[insert_at_zero_based - 1].strip():
                            prefix.append("\n")
                        suffix: List[str] = []
                        if (
                            insert_at_zero_based < len(lines)
                            and lines[insert_at_zero_based].strip()
                        ):
                            suffix.append("\n")
                        lines[insert_at_zero_based:insert_at_zero_based] = (
                            prefix + indented + suffix
                        )
                elif proposal.insert_into_class:
                    # Prepare method signature & decorator
                    method_kind = proposal.method_kind or "instance"
                    self._prepare_extracted_method_signature(
                        proposal.extracted_function,
                        method_kind,
                        proposal.method_param_name,
                    )
                    func_code = ast.unparse(proposal.extracted_function)
                    method_lines = [line + "\n" for line in func_code.split("\n")]
                    insert_info = self._find_class_insert_position(
                        "".join(lines), proposal.insert_into_class
                    )
                    if insert_info is None:
                        insert_line = self._find_insert_position(lines)
                        lines[insert_line:insert_line] = method_lines + ["\n", "\n"]
                    else:
                        insert_line_zero_based, indent = insert_info
                        method_indent = indent + "    "
                        indented_method: List[str] = []
                        for line in method_lines:
                            if line.strip():
                                indented_method.append(method_indent + line)
                            else:
                                indented_method.append(line)
                        insert_at = insert_line_zero_based + 1
                        method_prefix: List[str] = []
                        if insert_at > 0 and lines[insert_at - 1].strip():
                            method_prefix.append("\n")
                        method_suffix: List[str] = []
                        if insert_at < len(lines) and lines[insert_at].strip():
                            method_suffix.append("\n")
                        lines[insert_at:insert_at] = method_prefix + indented_method + method_suffix
                else:
                    func_code = ast.unparse(proposal.extracted_function)
                    func_lines = [line + "\n" for line in func_code.split("\n")]
                    insert_line = self._find_insert_position(lines)
                    lines_to_insert: List[str] = []
                    if insert_line > 0:
                        blank_lines_before = 0
                        check_line = insert_line - 1
                        while check_line >= 0 and not lines[check_line].strip():
                            blank_lines_before += 1
                            check_line -= 1
                        if blank_lines_before < 2:
                            lines_to_insert.extend(["\n"] * (2 - blank_lines_before))
                    lines_to_insert.extend(func_lines)
                    lines_to_insert.extend(["\n", "\n"])
                    lines[insert_line:insert_line] = lines_to_insert
            else:
                # Non-canonical file: insert import (module-level only)
                if not proposal.insert_into_class:
                    from_path = Path(proposal.file_path)
                    to_path = Path(file_path)
                    from pathlib import Path as _P

                    common_dir = _P(
                        __import__("os").path.commonpath([str(from_path), str(to_path)])
                    )
                    layout = ProjectLayout.discover(
                        common_dir,
                        prefer_absolute_imports=self.prefer_absolute_imports,
                        pep420_namespace_packages=self.pep420_namespace_packages,
                    )
                    abs_mod = layout.module_name_for(from_path)
                    if abs_mod and layout.prefer_absolute_imports:
                        module_name = abs_mod
                    else:
                        if from_path.parent == to_path.parent:
                            module_name = from_path.stem
                        else:
                            module_name = abs_mod or from_path.stem
                    func_name = proposal.extracted_function.name
                    import_line = f"from {module_name} import {func_name}\n"
                    if not any(import_line.strip() == ln.strip() for ln in lines):
                        import_pos = self._find_import_position(lines)
                        lines.insert(import_pos, import_line)

            modified_files[file_path] = "".join(lines)

        return modified_files

    def _find_import_position(self, lines: List[str]) -> int:
        """Find position to insert a new import statement."""
        last_import_line, after_docstring = self._scan_module_docstring_and_imports(lines)
        return last_import_line if last_import_line > 0 else after_docstring

    def _get_indent(self, line: str) -> str:
        """Get the indentation of a line."""
        return line[: len(line) - len(line.lstrip())]

    def _find_insert_position(self, lines: List[str]) -> int:
        """
        Find position to insert extracted function at END of file.

        This prevents line number shifts when multiple refactorings are applied,
        making sequential refactorings more robust.
        """
        # Insert at the end of the file
        # Find the last non-blank line
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                # Insert after this line
                return i + 1

        # Empty file - insert at beginning
        return 0

    def _find_class_insert_position(
        self, source: str, class_name: str
    ) -> Optional[Tuple[int, str]]:
        """
        Find insertion position (0-based line index) at end of class body and class indentation.

        Returns (insert_line_index, class_indent_str) or None.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        locator = ClassLocator(source, class_name)
        locator.visit(tree)
        return locator.result

    def _find_function_insert_position_before_body_statements(
        self, source: str, function_name: str
    ) -> Optional[Tuple[int, str]]:
        """
        Find an insertion position (0-based line index) inside the given function BEFORE
        executable body statements (i.e., after any docstring and after any leading
        nested defs), along with the function's indentation.

        This ensures the inserted helper is bound before returns/calls are executed.
        Returns (insert_line_index, function_indent_str) or None if function not found.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        locator = FuncLocator(source, function_name)
        locator.visit(tree)
        return locator.result

    def _get_block_indices(
        self, function: FunctionNode, block_nodes: List[ast.AST]
    ) -> Optional[Tuple[int, int]]:
        """
        Find the indices of a block within a function body.

        Args:
            function: Function definition
            block_nodes: Block to find

        Returns:
            (start_index, end_index) or None if not found
        """
        if not block_nodes:
            return None

        # Get function body (skip docstring)
        body = self._body_without_docstring(function.body)

        # Match by line numbers
        first_node = cast(Union[ast.stmt, ast.expr], block_nodes[0])
        last_node = cast(Union[ast.stmt, ast.expr], block_nodes[-1])
        block_start_line = first_node.lineno
        block_end_line = (
            last_node.end_lineno
            if hasattr(last_node, "end_lineno") and last_node.end_lineno is not None
            else last_node.lineno
        )

        # Find matching range in body
        for i, stmt in enumerate(body):
            stmt_start = stmt.lineno
            stmt_end = stmt.end_lineno if hasattr(stmt, "end_lineno") else stmt.lineno

            if stmt_start == block_start_line:
                # Found start, now find end
                for j in range(i, len(body)):
                    stmt_end = (
                        body[j].end_lineno if hasattr(body[j], "end_lineno") else body[j].lineno
                    )
                    if stmt_end == block_end_line:
                        return (i, j)

        return None

    def _are_structurally_similar(
        self,
        block1: List[ast.AST],
        block2: List[ast.AST],
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> bool:
        """
        Check if two blocks are structurally similar enough to attempt unification.

        This does a rough structural comparison to filter out obviously different blocks.

        Args:
            block1: First block
            block2: Second block
            threshold: Similarity threshold (0.0 to 1.0, default: 0.6)

        Returns:
            True if blocks are similar enough
        """
        if len(block1) != len(block2):
            return False

        total_nodes = 0
        matching_nodes = 0

        for stmt1, stmt2 in zip(block1, block2):
            # Compare AST structure
            nodes1 = list(ast.walk(stmt1))
            nodes2 = list(ast.walk(stmt2))

            # Must have similar number of nodes
            if abs(len(nodes1) - len(nodes2)) / max(len(nodes1), len(nodes2)) > 0.3:
                return False

            # Count matching node types
            types1 = [type(n).__name__ for n in nodes1]
            types2 = [type(n).__name__ for n in nodes2]

            # Count common types
            from collections import Counter

            counter1 = Counter(types1)
            counter2 = Counter(types2)

            common = sum((counter1 & counter2).values())
            total = max(len(types1), len(types2))

            total_nodes += total
            matching_nodes += common

        if total_nodes == 0:
            return False

        similarity = matching_nodes / total_nodes
        return similarity >= threshold

    def refactor_to_fixed_point(
        self, file_path: str, max_iterations: int = 10
    ) -> Tuple[str, int, List[str]]:
        """
            Apply refactorings iteratively until a fixed point is reached.

        This method applies refactorings one at a time, re-analyzing after each
        application. This prevents the sequential corruption bug where applying
        multiple refactorings at once causes line number misalignment.

        max_iterations semantics:
        - If max_iterations > 0, stop after at most that many applied refactorings.
        - If max_iterations <= 0, run until a natural fixed point (no proposals found).

            Args:
                file_path: Path to file to refactor
                max_iterations: Maximum iterations to prevent infinite loops

            Returns:
                Tuple of (final_code, num_refactorings_applied, descriptions)
        """
        current_code = Path(file_path).read_text()
        num_applied = 0
        descriptions = []

        iteration = 0
        while True:
            # Write current code to file for analysis
            with open(file_path, "w") as f:
                f.write(current_code)

            # Analyze for refactoring opportunities
            # Important: invalidate cached analysis for this path so we see latest edits
            proposals = self.analyze_files([file_path], invalidate_paths=[file_path])

            if not proposals:
                # Fixed point reached - no more refactorings found
                break

            # Apply only the first proposal
            proposal = proposals[0]
            new_code = self.apply_refactoring(file_path, proposal)
            # Idempotence guard: if no change, stop to avoid churn
            if new_code == current_code:
                break
            current_code = new_code
            num_applied += 1
            descriptions.append(proposal.description)

            iteration += 1
            if max_iterations > 0 and iteration >= max_iterations:
                break

        return current_code, num_applied, descriptions

    def refactor_directory_to_fixed_point(
        self,
        input_dir: str,
        output_dir: str,
        max_iterations: int = 10,
        progress: str = "tqdm",
    ) -> Tuple[Dict[str, Tuple[int, List[str]]], str]:
        """
        Apply refactorings across a directory (recursively) until a fixed point.

        Unlike the per-file variant, this performs whole-project analysis on every
        iteration so it can apply BOTH same-file and cross-file proposals. One proposal
        is applied per iteration, then the directory is re-analyzed, up to max_iterations.

        Args:
            input_dir: Input directory path
            output_dir: Output directory path (results are written here)
            max_iterations: Maximum iterations to prevent infinite loops. If <= 0,
                run until a natural fixed point (no proposals remain).
            progress: Progress display mode: 'tqdm' for percentage bar if available,
                'auto' fallback to simple inline bar, 'none' disables progress output.

        Returns:
            (results_dict, termination_reason)
            termination_reason ∈ {"fixed_point", "iteration_cap"}
        """
        from pathlib import Path
        import shutil
        import textwrap

        progress_mode, tqdm_wrapper, use_tqdm = self._resolve_progress_backend(progress)

        input_path = Path(input_dir)
        output_path = Path(output_dir)

        # Create output directory
        output_path.mkdir(parents=True, exist_ok=True)

        # Copy all files to output directory first (skip if same path)
        if input_path != output_path:
            for item in input_path.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(input_path)
                    output_file = output_path / rel_path
                    output_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, output_file)

        # Aggregate results per file
        results: Dict[str, Tuple[int, List[str]]] = {}

        def _bump_result(path: str, desc: str) -> None:
            count, descs = results.get(path, (0, []))
            results[path] = (count + 1, descs + [desc])

        # Proposal processing state
        proposal_queue: List[RefactoringProposal] = []
        iterations = 0
        total_applied = 0
        termination_reason = "fixed_point"

        # Timing / ETA state (for heuristic ETA when total unknown)
        import time as _time

        per_proposal_durations: List[float] = []

        # Progress helpers -------------------------------------------------
        progress_bar = None

        # Fallback inline bar (only when not using tqdm and not in detail/none)
        def _fallback_bar(applied: int, queued: int, phase: str, desc: str) -> None:
            # Suppress inline fallback bar when tqdm is selected or active, or in 'none'/'detail' modes
            if progress_mode in ("none", "detail", "tqdm") or use_tqdm:
                return
            try:
                denom = max(applied + queued, 1)
                pct = int((applied / denom) * 100)
                bar = self._render_inline_bar(pct, bar_len=32)
                short = desc if len(desc) <= 48 else desc[:45] + "..."
                print(
                    f"\r[towel] {phase:<10} [{bar}] {pct:3d}% | applied={applied} queued={queued} | {short}",
                    end="",
                    flush=True,
                )
            except Exception:
                pass

        def _update_progress_postfix(applied: int, queued: int) -> None:
            """Keep tqdm postfix updates consistent (see docs/DRY_RUN_2025-11-28.md)."""
            if not (use_tqdm and progress_bar is not None):
                return
            try:
                progress_bar.set_postfix({"A": applied, "Q": queued}, refresh=True)
            except Exception:
                pass

        def _apply_proposal_and_refresh_queue(
            proposal: RefactoringProposal, queue: List[RefactoringProposal]
        ) -> List[RefactoringProposal]:
            """Apply proposal, update caches, and refresh queue (DRY helper)."""
            result = self.apply_refactoring_multi_file(proposal)
            if isinstance(result, tuple):
                modified_files, changed_paths = result
            else:
                modified_files = result
                changed_paths = list(modified_files.keys())

            for fpath, content in modified_files.items():
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(content)
                _bump_result(fpath, proposal.description)

            if hasattr(self, "invalidate_paths") and changed_paths:
                try:
                    self.invalidate_paths(changed_paths)
                except Exception:
                    pass

            if changed_paths:
                changed_set = set(map(str, changed_paths))
                queue = [
                    p
                    for p in queue
                    if not any(
                        (rep.file_path or p.file_path) in changed_set for rep in p.replacements
                    )
                ]

                try:
                    localized = self.analyze_files(
                        list(changed_paths), invalidate_paths=list(changed_paths)
                    )
                    if localized:
                        localized = filter_overlapping_proposals(localized)
                        localized = [
                            p
                            for p in localized
                            if any(
                                (rep.file_path or p.file_path) in changed_set
                                for rep in p.replacements
                            )
                        ]
                        if localized:
                            queue = localized + queue
                            _detail(f"Localized +{len(localized)} follow-up(s)")
                            _fallback_bar(
                                total_applied,
                                len(queue),
                                "localized",
                                f"+{len(localized)} follow-ups",
                            )
                except Exception:
                    pass

            return queue

        # Note: We defer tqdm progress bar creation until we have proposals to apply.
        # This avoids an early line like "analyzing: 0it" with unknown totals.

        def _detail(msg: str) -> None:
            if progress_mode == "detail":
                print(f"[towel] {msg}")

        # Main loop -------------------------------------------------------
        while True:
            if not proposal_queue:
                # Global analysis pass
                if progress_mode != "none":
                    try:
                        file_count = sum(1 for _ in output_path.rglob("*.py"))
                        _detail(f"Analyzing {file_count} file(s)...")
                    except Exception:
                        pass
                # Show pairing progress during global analysis if user requested progress bars.
                analysis_progress_flag = (
                    progress_mode if progress_mode in ("tqdm", "auto") else "none"
                )
                proposals = self.analyze_directory(
                    str(output_path), recursive=True, verbose=False, progress=analysis_progress_flag
                )
                if not proposals:
                    # Fixed point reached
                    if use_tqdm and progress_bar is not None:
                        # Ensure a clean newline so the last line doesn't meld with following prints
                        try:
                            progress_bar.refresh()
                            progress_bar.close()
                        except Exception:
                            pass
                    else:
                        if progress_mode not in ("none", "detail") and not use_tqdm:
                            print()  # finish inline bar line
                    break
                proposal_queue = filter_overlapping_proposals(proposals)
                _detail(f"Discovered {len(proposal_queue)} proposal(s)")
                if progress_mode == "detail":
                    for i, p in enumerate(proposal_queue[:25], 1):  # cap verbose listing
                        short = textwrap.shorten(p.description, width=100, placeholder="...")
                        print(f"    {i:2d}. {short}")
                    if len(proposal_queue) > 25:
                        print(f"    ... {len(proposal_queue)-25} more")
                _fallback_bar(total_applied, len(proposal_queue), "discovered", "proposals queued")
                if use_tqdm and progress_bar is None:
                    # Lazily create tqdm now that we have proposals to apply
                    assert tqdm_wrapper is not None
                    try:
                        total_known = max_iterations > 0
                        # Use leave=False so subsequent prints don't duplicate the bar line.
                        progress_bar = tqdm_wrapper(
                            total=max_iterations if total_known else None,
                            desc="apply",
                            unit="it",
                            dynamic_ncols=True,
                            leave=False,
                        )
                        queued_ct = len(proposal_queue)
                        _update_progress_postfix(0, queued_ct)
                    except Exception:
                        progress_bar = None

            if not proposal_queue:
                break

            proposal = self._pop_next_proposal(proposal_queue)
            if proposal is None:
                break
            iter_start = _time.time()
            last_desc = proposal.description
            # Suppress separate applying log line when tqdm active to avoid duplicate lines
            if not (use_tqdm and progress_bar is not None):
                _fallback_bar(
                    total_applied, len(proposal_queue), "apply", f"#{iterations+1}: {last_desc}"
                )

            # Apply proposal
            proposal_queue = _apply_proposal_and_refresh_queue(proposal, proposal_queue)

            # Record duration for this iteration (include localized follow-up analysis time)
            per_proposal_durations.append(_time.time() - iter_start)
            iterations += 1
            total_applied += 1
            if use_tqdm and progress_bar is not None:
                try:
                    progress_bar.update(1)
                    queued_ct = len(proposal_queue)
                except Exception:
                    pass
                else:
                    _update_progress_postfix(total_applied, queued_ct)
            else:
                _fallback_bar(
                    total_applied, len(proposal_queue), "applied", f"#{iterations}: {last_desc}"
                )

            if max_iterations > 0 and iterations >= max_iterations:
                termination_reason = "iteration_cap"
                if use_tqdm and progress_bar is not None:
                    progress_bar.close()
                else:
                    if progress_mode not in ("none", "detail") and not use_tqdm:
                        print()
                break

        return results, termination_reason

    # Optional analysis cache invalidation hook used by directory fixed-point runner
    def invalidate_paths(self, paths: List[str]) -> None:  # pragma: no cover - simple cache hook
        try:
            from . import pipeline as _pipeline  # local import to avoid cycles at module load time

            for p in paths:
                if hasattr(_pipeline, "_analysis_cache"):
                    cache = cast(Dict[str, Any], getattr(_pipeline, "_analysis_cache"))
                    cache.pop(p, None)
        except Exception:
            # Non-fatal; best-effort invalidation only
            pass


def _refactor_engine_helper_2(self, all_functions, block_pairs, class_infos, progress, verbose):
    return self._evaluate_pairs_serial(
        block_pairs, all_functions, class_infos, verbose=verbose, progress=progress
    )


# Utility functions for overlap filtering


def get_affected_lines(proposal: RefactoringProposal) -> Set[Tuple[str, int]]:
    """
    Get all (file_path, line_number) tuples affected by a proposal.

    This is used for overlap detection - two proposals overlap if they
    affect any of the same lines in the same file.

    Args:
        proposal: A refactoring proposal

    Returns:
        Set of (file_path, line_number) tuples that would be modified
    """
    affected: Set[Tuple[str, int]] = set()
    for repl in proposal.replacements:
        file_path = repl.file_path or proposal.file_path
        start_line, end_line = repl.line_range
        for line_num in range(start_line, end_line + 1):
            affected.add((file_path, line_num))

    return affected


def filter_overlapping_proposals(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    """
    Filter proposals to remove overlaps, keeping the best ones.

    When multiple proposals affect overlapping lines, this function selects
    a subset of non-overlapping proposals. Larger proposals (affecting more
    lines) are preferred over smaller ones.

    Strategy:
    1. Sort proposals by size (total lines affected), largest first
    2. Greedily select proposals that don't overlap with already-selected ones

    Args:
        proposals: List of refactoring proposals

    Returns:
        List of non-overlapping proposals, sorted by size (largest first)

    Example:
        If proposals affect lines [1-7], [1-6], and [2-7], only [1-7]
        would be selected as it's the largest and the others overlap with it.
    """
    if not proposals:
        return []

    def proposal_size(p: RefactoringProposal) -> int:
        """Calculate total lines affected by a proposal."""
        total_lines = 0
        for repl in p.replacements:
            start_line, end_line = repl.line_range
            total_lines += end_line - start_line + 1
        return total_lines

    # Optional debug diagnostics: env flag
    import os as _os

    _debug_overlap = bool(_os.getenv("DEBUG_OVERLAP_FILTER"))

    # Helpers for deterministic ordering and interval extraction
    def first_span(p: RefactoringProposal) -> Tuple[str, int]:
        if not p.replacements:
            return (p.file_path, 0)
        starts = [r.line_range[0] for r in p.replacements]
        return (p.file_path, min(starts))

    # Map proposals to affected lines and per-file convex-hull intervals
    prop_affected: Dict[int, Set[Tuple[str, int]]] = {}
    prop_files: Dict[int, Set[str]] = {}
    per_file_interval: Dict[int, Dict[str, Tuple[int, int]]] = {}

    for idx, p in enumerate(proposals):
        affected = get_affected_lines(p)
        prop_affected[idx] = affected
        files: Set[str] = set(fp for (fp, _ln) in affected)
        prop_files[idx] = files
        per_file_interval[idx] = {}
        # Build convex hull interval per file for MWIS approximation
        by_file: Dict[str, List[int]] = {}
        for fp, ln in affected:
            by_file.setdefault(fp, []).append(ln)
        for fp, lines in by_file.items():
            per_file_interval[idx][fp] = (min(lines), max(lines))

    # Stage 1: Optimal non-overlapping selection within single-file proposals using MWIS
    selected_indices: Set[int] = set()
    used_lines: Set[Tuple[str, int]] = set()

    # Group single-file proposals by that file
    by_primary_file: Dict[str, List[int]] = {}
    for idx, files in prop_files.items():
        if len(files) == 1:
            fp = next(iter(files))
            by_primary_file.setdefault(fp, []).append(idx)

    def run_weighted_interval_scheduling(file_path: str, indices: List[int]) -> List[int]:
        # Build items: (start, end, weight, idx)
        items: List[Tuple[int, int, int, int, Tuple[str, int]]] = []
        for idx in indices:
            start, end = per_file_interval[idx][file_path]
            weight = proposal_size(proposals[idx])
            items.append((start, end, weight, idx, first_span(proposals[idx])))

        # Sort by end then tie-breaker to stabilize
        items.sort(key=lambda t: (t[1], t[0], -t[2], t[4]))

        n = len(items)
        if n == 0:
            return []

        # Precompute p[j]: rightmost non-overlapping interval index before j
        ends = [it[1] for it in items]
        starts = [it[0] for it in items]
        p = [-1] * n
        j = 0
        for j in range(n):
            # binary search for last i with ends[i] < starts[j]
            lo, hi = 0, j - 1
            last = -1
            while lo <= hi:
                mid = (lo + hi) // 2
                if ends[mid] < starts[j]:
                    last = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            p[j] = last

        # DP arrays
        dp = [0] * n
        take = [False] * n
        for j in range(n):
            wj = items[j][2]
            without = dp[j - 1] if j > 0 else 0
            withj = wj + (dp[p[j]] if p[j] != -1 else 0)
            if withj > without:
                dp[j] = withj
                take[j] = True
            elif withj == without:
                # Tie-breaker: prefer earlier ending interval set implicitly
                dp[j] = without
                take[j] = False
            else:
                dp[j] = without
                take[j] = False

        # Reconstruct
        sel: List[int] = []
        j = n - 1
        while j >= 0:
            if take[j]:
                sel.append(items[j][3])
                j = p[j]
            else:
                j -= 1
        sel.reverse()
        if _debug_overlap:
            try:
                print(
                    f"OVERLAP_OPTIMAL file={file_path} selected={len(sel)} total_weight={dp[n-1]} candidates={n}"
                )
            except Exception:
                pass
        return sel

    for fp, idxs in by_primary_file.items():
        chosen = run_weighted_interval_scheduling(fp, idxs)
        for idx in chosen:
            if idx not in selected_indices:
                selected_indices.add(idx)
                used_lines.update(prop_affected[idx])

    # Stage 2: Greedy add for remaining proposals (multi-file or leftover), respecting used_lines
    def sort_key(p: RefactoringProposal) -> Tuple[int, Tuple[str, int]]:
        return (-(proposal_size(p)), first_span(p))

    remaining = [i for i in range(len(proposals)) if i not in selected_indices]
    remaining_sorted = sorted(remaining, key=lambda i: sort_key(proposals[i]))

    selected: List[RefactoringProposal] = [proposals[i] for i in selected_indices]

    for idx in remaining_sorted:
        affected = prop_affected[idx]
        intersection = affected & used_lines
        if not intersection:
            selected.append(proposals[idx])
            used_lines.update(affected)
        elif _debug_overlap:
            by_file2: Dict[str, List[int]] = {}
            for fp, ln in intersection:
                by_file2.setdefault(fp, []).append(ln)
            parts = [
                f"{fp}:{min(lines)}-{max(lines)} ({len(lines)} lines)"
                for fp, lines in by_file2.items()
            ]
            try:
                print(
                    "OVERLAP_DROP: size=",
                    proposal_size(proposals[idx]),
                    " first=",
                    first_span(proposals[idx]),
                    " because intersects ",
                    "; ".join(parts),
                )
            except Exception:
                pass

    # Return selected sorted by size descending for external stability
    return sorted(selected, key=lambda p: (-(proposal_size(p)), first_span(p)))
