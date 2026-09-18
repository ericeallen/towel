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
responsibility (placement, reuse, insertion, annotation wiring,
materialization, clustering, parallel evaluation, the fixed-point drivers).
Each mixin is a class in its own module and reaches the rest of the engine
only through the attributes and methods declared here, so a reader of one
module sees exactly what it depends on, and mypy checks that the engine
provides it. The engine's constructor assigns every attribute; the method
stubs are implemented by the engine or by another mixin.
"""

from __future__ import annotations

import ast
from typing import Callable, Dict, FrozenSet, List, Literal, Optional, Sequence, Set, Tuple

from ..type_inference import TypeOracle
from .models import AppliedChange, FunctionArtifact, RefactoringProposal, ReusedFunction
from .semantic_safety import ImportGraphCache


class EngineState:
    """Attributes and operations shared across the engine's mixins (declarations only)."""

    _source_lines_cache: Dict[str, Tuple[Tuple[int, int], Tuple[str, ...]]]
    """Lines of files the apply path read, keyed by path, with the stat they were read at."""

    import_graph: ImportGraphCache
    """What this run has learned about the project's import graph."""

    _helper_name_counters: Dict[str, int]
    """Next helper number per file, so generated names are unique across a run."""

    incremental_global_passes: bool
    """Whether later global passes re-pair only the files rewritten since the last one."""

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

    type_inferrer: Optional[TypeOracle]
    """The project's type checker, when one is installed and wanted."""

    def _get_indent(self, line: str) -> str:
        """The indentation of a line; provided by InsertionPoints."""
        raise NotImplementedError

    @classmethod
    def placeable_after(cls, source: str) -> Set[str]:
        """Module-level definitions a helper may follow; provided by InsertionPoints."""
        raise NotImplementedError

    @staticmethod
    def _innermost_function_at(
        file_path: str,
        line_range: Tuple[int, int],
        all_functions: Sequence[FunctionArtifact],
    ) -> Optional[FunctionArtifact]:
        """The innermost function containing a line range; provided by ExistingFunctionReuse."""
        raise NotImplementedError

    def _allocate_helper_name(
        self,
        file_path: str,
        *,
        class_context: bool = False,
        related_paths: Sequence[str] = (),
    ) -> str:
        """Provided by the engine."""
        raise NotImplementedError

    @staticmethod
    def _annotation_names(helper: ast.FunctionDef) -> Set[str]:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _checks_generated_types(self, proposal: RefactoringProposal) -> bool:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _find_class_insert_position(
        self, source: str, class_name: str
    ) -> Optional[Tuple[int, str]]:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _find_function_insert_position_before_body_statements(
        self, source: str, function_name: str
    ) -> Optional[Tuple[int, str]]:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _find_import_position(self, lines: List[str]) -> int:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _find_insert_position(
        self, lines: List[str], after_names: Optional[Set[str]] = None
    ) -> int:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _infer_helper_annotations(self, proposal: RefactoringProposal) -> None:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _introduces_type_errors(self, modified_files: Dict[str, str]) -> bool:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _prepare_extracted_method_signature(
        self,
        fn: ast.FunctionDef,
        method_kind: Literal["instance", "classmethod", "staticmethod"],
        implicit_param: Optional[str],
    ) -> None:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    @staticmethod
    def _retarget_helper_calls(node: ast.AST, original_name: str, final_name: str) -> ast.AST:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    def _rewrite_call_for_method(
        self,
        node: ast.AST,
        original_name: str,
        new_name: str,
        method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]],
        implicit_param: Optional[str],
        class_name: Optional[str],
        receiver_parameter_index: Optional[int] = None,
        helper_parameter_count: Optional[int] = None,
    ) -> ast.AST:
        """Provided by HelperPlacement."""
        raise NotImplementedError

    def _source_lines(self, file_path: str) -> Sequence[str]:
        """Provided by InsertionPoints."""
        raise NotImplementedError

    def _verify_reused_function_calls(
        self,
        modified_files: Dict[str, str],
        target: ReusedFunction,
        proposal: RefactoringProposal,
    ) -> None:
        """Provided by ExistingFunctionReuse."""
        raise NotImplementedError

    @staticmethod
    def _with_every_annotation_any(proposal: RefactoringProposal) -> RefactoringProposal:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    @staticmethod
    def _without_annotations(proposal: RefactoringProposal) -> RefactoringProposal:
        """Provided by HelperAnnotationWiring."""
        raise NotImplementedError

    def _find_python_files(self, directory: str, recursive: bool = True) -> List[str]:
        """Provided by the engine."""
        raise NotImplementedError

    def analyze_directory(
        self,
        directory: str,
        recursive: bool = True,
        *,
        verbose: bool = False,
        progress: str = "tqdm",
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Provided by the engine."""
        raise NotImplementedError

    def analyze_files(
        self,
        file_paths: List[str],
        *,
        verbose: bool = False,
        progress: str = "tqdm",
        invalidate_paths: Optional[List[str]] = None,
        changed_files: Optional[FrozenSet[str]] = None,
    ) -> List[RefactoringProposal]:
        """Provided by the engine."""
        raise NotImplementedError

    def apply_refactoring_multi_file(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Provided by Materialization."""
        raise NotImplementedError

    def apply_refactoring(self, file_path: str, proposal: RefactoringProposal) -> str:
        """Provided by Materialization."""
        raise NotImplementedError

    def invalidate_paths(self, paths: List[str]) -> None:
        """Provided by the engine."""
        raise NotImplementedError
