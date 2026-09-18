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

"""Turning an accepted proposal into the modified files.

Materialization owns its syntax trees: the proposal is copied, annotated,
rendered through the project's formatter, and each file is rebuilt with the
helper inserted and every call site replaced, then the generated Python is
compiled and every generated call is checked to bind the helper's
signature. With a type checker installed the annotated variant is tried
first and falls back to Any and then to no annotations when it introduces a
type error. The immutable byte plan is applied transactionally by
changes.py.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import os
import re
import textwrap

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from .exceptions import RefactoringError
from .insertion import reindent, relative_import_module
from .models import AppliedChange, MethodKind, RefactoringProposal, Replacement
from ..project_layout import ProjectLayout, is_package_dir
from towel.changes import ChangeConflict, ChangePlan
from ..source_text import read_source

from .reuse import ExistingFunctionReuse
from .annotation_wiring import HelperAnnotationWiring
from .insertion import InsertionPoints
from .placement import HelperPlacement


@dataclass(frozen=True)
class _HelperNaming:
    """The helper's name before and after allocation, and where its receiver sits."""

    original_name: str
    final_name: str
    receiver_parameter_index: Optional[int]
    parameter_count: int


def _padded(lines: List[str], insert_at: int, block: List[str]) -> List[str]:
    """``block`` with a blank line on each side that touches a non-blank line of ``lines``."""
    prefix = ["\n"] if insert_at > 0 and lines[insert_at - 1].strip() else []
    suffix = ["\n"] if insert_at < len(lines) and lines[insert_at].strip() else []
    return prefix + block + suffix


class Materialization(
    InsertionPoints, HelperPlacement, ExistingFunctionReuse, HelperAnnotationWiring
):
    """Materialization methods of the engine; see the module docstring."""

    def apply_refactoring(self, file_path: str, proposal: RefactoringProposal) -> str:
        """
        Apply a refactoring proposal to a file.

        Args:
            file_path: Path to file
            proposal: Refactoring proposal

        Returns:
            Modified source code
        """
        return self.apply_refactoring_multi_file(proposal).get(file_path, "")

    def plan_refactoring(self, proposal: RefactoringProposal) -> ChangePlan:
        """Materialize a proposal into an immutable, stale-checked byte plan."""
        paths = {
            proposal.file_path,
            *(rep.file_path or proposal.file_path for rep in proposal.replacements),
        }
        before = {path: Path(path).read_bytes() for path in paths}
        after = self.apply_refactoring_multi_file(proposal)
        for path in paths:
            if Path(path).read_bytes() != before[path]:
                raise ChangeConflict(f"Source changed during planning: {path}")
        return ChangePlan.from_sources(before, after)

    def apply_refactoring_multi_file(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Render without writing or mutating caller-owned proposal ASTs."""
        for path, digest in proposal.source_digests:
            if hashlib.sha256(read_source(path).encode("utf-8")).hexdigest() != digest:
                raise ChangeConflict(f"Stale proposal; analyze again: {path}")
        counters = self._helper_name_counters.copy()
        try:
            return self._materialize_refactoring(proposal)
        finally:
            self._helper_name_counters = counters

    def _render(self, node: ast.AST) -> str:
        """The source text inserted for a generated node, formatted when a formatter is set."""
        source = ast.unparse(node)
        if self.snippet_formatter is None:
            return source
        return self.snippet_formatter(source)

    def _materialize_refactoring(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """
        Apply a cross-file refactoring proposal.

        Args:
            proposal: Refactoring proposal

        Returns:
            Dict mapping file paths to modified source code
        """
        # Materialization owns its ASTs; callers may reuse or inspect the proposal.
        proposal = copy.deepcopy(proposal)
        self._infer_helper_annotations(proposal)
        variants = [proposal]
        if self._checks_generated_types(proposal):
            variants += [
                self._with_every_annotation_any(proposal),
                self._without_annotations(proposal),
            ]
        counters = dict(self._helper_name_counters)
        for index, variant in enumerate(variants):
            mark = len(self._change_log)
            # Each attempt allocates the helper's name; restore the counters so
            # every attempt gets the same name and none is consumed by a retry.
            self._helper_name_counters = dict(counters)
            files = self._materialize_once(variant)
            if index == len(variants) - 1 or not self._introduces_type_errors(files):
                return files
            del self._change_log[mark:]
        raise AssertionError("unreachable: the bare variant is always accepted")

    def _materialize_once(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Render one proposal into modified sources (see ``_materialize_refactoring``)."""
        replacements_by_file: Dict[str, List[Replacement]] = {}
        for repl in proposal.replacements:
            file_path = repl.file_path or proposal.file_path
            replacements_by_file.setdefault(file_path, []).append(repl)
        # The canonical file is always processed, so the helper is emitted
        # even when every call site is elsewhere.
        replacements_by_file.setdefault(proposal.file_path, [])

        naming = self._settle_helper_name(proposal, list(replacements_by_file))
        modified_files = {
            file_path: self._materialize_file(proposal, naming, file_path, replacements)
            for file_path, replacements in replacements_by_file.items()
        }

        for path, content in modified_files.items():
            compile(content, path, "exec")
        if proposal.reused_function is not None:
            self._verify_reused_function_calls(modified_files, proposal.reused_function, proposal)
        else:
            self._verify_helper_call_arity(modified_files, naming.final_name, proposal.method_kind)
        return modified_files

    def _settle_helper_name(
        self, proposal: RefactoringProposal, related_paths: List[str]
    ) -> _HelperNaming:
        """Fix the helper's final name once, before any file is rendered.

        A generated name is allocated against every file the proposal touches
        so it stays unique across them; a user-provided name is kept, made
        non-public inside a class; a reused function keeps the name the calls
        already use.
        """
        original_name = proposal.extracted_function.name
        class_context = bool(proposal.insert_into_class) or any(
            replacement.class_name is not None for replacement in proposal.replacements
        )
        if proposal.reused_function is not None:
            pass  # the calls already name an existing function; it keeps its name
        elif original_name == "__extracted_func" or (
            class_context and re.fullmatch(r"__extracted_func(?:_\d+)?", original_name)
        ):
            proposal.extracted_function.name = self._allocate_helper_name(
                proposal.file_path, class_context=class_context, related_paths=related_paths
            )
        elif proposal.insert_into_class and not original_name.startswith("_"):
            proposal.extracted_function.name = f"_{original_name}"
        receiver_name = proposal.method_param_name or (
            "cls" if proposal.method_kind == "classmethod" else "self"
        )
        parameters = [arg.arg for arg in proposal.extracted_function.args.args]
        return _HelperNaming(
            original_name=original_name,
            final_name=proposal.extracted_function.name,
            receiver_parameter_index=(
                parameters.index(receiver_name) if receiver_name in parameters else None
            ),
            parameter_count=len(parameters),
        )

    def _materialize_file(
        self,
        proposal: RefactoringProposal,
        naming: _HelperNaming,
        file_path: str,
        replacements: List[Replacement],
    ) -> str:
        """One file's new source: its call sites spliced in, then the helper or its import."""
        lines = list(self._source_lines(file_path))
        self._splice_call_sites(proposal, naming, file_path, lines, replacements)
        if proposal.reused_function is not None and file_path == proposal.file_path:
            pass  # the function the calls target is already defined here
        elif file_path == proposal.file_path:
            self._insert_helper(proposal, file_path, lines)
        elif not proposal.insert_into_class:
            self._insert_helper_import(proposal, file_path, lines)
        assembled = "".join(lines)
        if self.file_finisher is not None:
            assembled = self.file_finisher(file_path, assembled)
        return assembled

    def _splice_call_sites(
        self,
        proposal: RefactoringProposal,
        naming: _HelperNaming,
        file_path: str,
        lines: List[str],
        replacements: List[Replacement],
    ) -> None:
        """Replace each duplicate block of ``lines`` with its generated call, last first."""
        ascending = sorted(replacements, key=lambda replacement: replacement.line_range)
        if any(
            left.line_range[1] >= right.line_range[0]
            for left, right in zip(ascending, ascending[1:])
        ):
            raise ValueError(f"Overlapping replacements: {file_path}")
        # Splicing from the bottom up keeps every earlier range valid.
        for repl in reversed(ascending):
            start_line, end_line = repl.line_range
            if not 1 <= start_line <= end_line <= len(lines):
                raise ValueError(f"Invalid replacement range {start_line}-{end_line}: {file_path}")
            replacement_code = self._render(self._call_site(proposal, naming, repl))
            indent = self._get_indent(lines[start_line - 1])
            replacement_lines = [
                indent + line + "\n" if line.strip() else "\n"
                for line in replacement_code.split("\n")
            ]
            # A call to an existing function needs no naming, so it is not logged.
            if proposal.reused_function is None:
                self._change_log.append(
                    AppliedChange(
                        helper=naming.final_name,
                        path=file_path,
                        line=start_line,
                        before=textwrap.dedent("".join(lines[start_line - 1 : end_line])).rstrip(
                            "\n"
                        ),
                        after=replacement_code,
                    )
                )
            lines[start_line - 1 : end_line] = replacement_lines

    def _call_site(
        self, proposal: RefactoringProposal, naming: _HelperNaming, repl: Replacement
    ) -> ast.AST:
        """The replacement's call, retargeted to the helper's final name and placement."""
        node = copy.deepcopy(repl.node)
        if proposal.insert_into_class and repl.class_name:
            return self._rewrite_call_for_method(
                node,
                naming.original_name,
                naming.final_name,
                repl.method_kind or proposal.method_kind,
                repl.implicit_param or proposal.method_param_name,
                repl.class_name,
                naming.receiver_parameter_index,
                naming.parameter_count,
            )
        if naming.original_name != naming.final_name:
            return self._retarget_helper_calls(node, naming.original_name, naming.final_name)
        return node

    def _insert_helper(
        self, proposal: RefactoringProposal, file_path: str, lines: List[str]
    ) -> None:
        """Insert the helper into the canonical file's ``lines`` where the proposal places it."""
        # Names an inferred annotation needs that the module does not bind.
        for module_name, name in proposal.required_imports:
            self._ensure_import(lines, module_name, name)
        if proposal.insert_into_function:
            self._insert_helper_into_function(proposal, lines)
        elif proposal.insert_into_class:
            self._insert_helper_into_class(proposal, file_path, lines)
        else:
            self._insert_helper_at_module_level(proposal, lines)

    def _insert_helper_into_function(self, proposal: RefactoringProposal, lines: List[str]) -> None:
        assert proposal.insert_into_function is not None
        fn_insert_info = self._find_function_insert_position_before_body_statements(
            "".join(lines), proposal.insert_into_function
        )
        fn_lines = [line + "\n" for line in self._render(proposal.extracted_function).split("\n")]
        if fn_insert_info is None:
            insert_line = self._find_insert_position(lines)
            lines[insert_line:insert_line] = fn_lines + ["\n", "\n"]
            return
        insert_at, indent = fn_insert_info
        inner_indent = indent + "    "
        indented = [inner_indent + line if line.strip() else line for line in fn_lines]
        lines[insert_at:insert_at] = _padded(lines, insert_at, indented)

    def _insert_helper_into_class(
        self, proposal: RefactoringProposal, file_path: str, lines: List[str]
    ) -> None:
        assert proposal.insert_into_class is not None
        self._prepare_extracted_method_signature(
            proposal.extracted_function,
            proposal.method_kind or "instance",
            proposal.method_param_name,
        )
        method_lines = [
            line + "\n" for line in self._render(proposal.extracted_function).split("\n")
        ]
        insert_info = self._find_class_insert_position("".join(lines), proposal.insert_into_class)
        if insert_info is None:
            raise RefactoringError(
                f"Class {proposal.insert_into_class} is not a unique module-level "
                f"class in {file_path}; cannot insert a method"
            )
        insert_line_zero_based, method_indent = insert_info
        indented = [
            reindent(line, method_indent) if line.strip() else line for line in method_lines
        ]
        insert_at = insert_line_zero_based + 1
        lines[insert_at:insert_at] = _padded(lines, insert_at, indented)

    def _insert_helper_at_module_level(
        self, proposal: RefactoringProposal, lines: List[str]
    ) -> None:
        func_lines = [line + "\n" for line in self._render(proposal.extracted_function).split("\n")]
        insert_line = self._find_insert_position(
            lines, self._annotation_names(proposal.extracted_function)
        )
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

    def _insert_helper_import(
        self, proposal: RefactoringProposal, file_path: str, lines: List[str]
    ) -> None:
        """Import the module-level helper into a file whose call sites need it."""
        from_path = Path(proposal.file_path)
        to_path = Path(file_path)
        common_dir = Path(os.path.commonpath([str(from_path), str(to_path)]))
        layout = ProjectLayout.discover(
            common_dir,
            prefer_absolute_imports=self.prefer_absolute_imports,
            pep420_namespace_packages=self.pep420_namespace_packages,
        )
        abs_mod = layout.module_name_for(from_path)
        relative = relative_import_module(from_path, to_path)
        # A relative import only resolves inside a classic package; flat
        # modules on sys.path (no __init__.py) must use an absolute name.
        importer_in_package = is_package_dir(to_path.parent)
        # Prefer an absolute import only when the layout is anchored by
        # real packaging metadata, so the name stays valid after an
        # out-of-place output is adopted into its real location. Otherwise
        # use a relative import when the file is in a package: it encodes
        # only the intrinsic same-package relationship, is valid wherever
        # the code lands, and matches the surrounding intra-package style.
        if abs_mod and layout.prefer_absolute_imports and layout.metadata_root:
            module_name = abs_mod
        elif relative is not None and importer_in_package:
            module_name = relative
        else:
            module_name = abs_mod or from_path.stem
        self._ensure_import(lines, module_name, proposal.extracted_function.name)

    def _ensure_import(self, lines: List[str], module_name: str, name: str) -> None:
        """Add ``from module_name import name`` at the import position unless a line already says so."""
        import_line = f"from {module_name} import {name}\n"
        if not any(import_line.strip() == ln.strip() for ln in lines):
            lines.insert(self._find_import_position(lines), import_line)

    def _verify_helper_call_arity(
        self,
        modified_files: Dict[str, str],
        helper_name: str,
        method_kind: Optional[MethodKind],
    ) -> None:
        """Fail loudly if any generated call cannot bind to the generated helper.

        Method conversion and receiver removal happen after the instantiation
        check, so this compares the rendered helper signature with every call
        that names it. Bound calls omit the receiver; static calls do not.
        """
        parameters: Optional[int] = None
        for source in modified_files.values():
            for node in ast.walk(self._parse_source(source)):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == helper_name
                ):
                    parameters = len(node.args.posonlyargs) + len(node.args.args)
        if parameters is None:
            raise RefactoringError(f"Helper {helper_name} was not emitted")
        for path, source in modified_files.items():
            for node in ast.walk(self._parse_source(source)):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if isinstance(function, ast.Name) and function.id == helper_name:
                    expected = parameters
                elif isinstance(function, ast.Attribute) and function.attr == helper_name:
                    expected = parameters if method_kind == "staticmethod" else parameters - 1
                else:
                    continue
                if node.keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
                    continue
                if len(node.args) != expected:
                    raise RefactoringError(
                        f"Generated call to {helper_name} passes {len(node.args)} arguments "
                        f"but the helper binds {expected}: {path}"
                    )
