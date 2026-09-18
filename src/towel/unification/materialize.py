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

from pathlib import Path
from typing import Dict, List, Literal, Optional
from .exceptions import RefactoringError
from .insertion import reindent, relative_import_module
from .models import AppliedChange, RefactoringProposal, Replacement
from .pipeline import parse_cached
from .project_layout import ProjectLayout, is_package_dir
from towel.changes import ChangeConflict, ChangePlan

from .engine_state import EngineState


class Materialization(EngineState):
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
        # All proposals now use multi-file format
        modified_files = self.apply_refactoring_multi_file(proposal)
        # Backward-compat: handle tuple return (modified_files, changed_paths)
        if isinstance(modified_files, tuple):
            modified_files = modified_files[0]
        # Return the content for the requested file
        return modified_files.get(file_path, "")

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
            if (
                hashlib.sha256(Path(path).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
                != digest
            ):
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
        class_context = bool(proposal.insert_into_class) or any(
            replacement.class_name is not None for replacement in proposal.replacements
        )
        if proposal.reused_function is not None:
            pass  # the calls already name an existing function; it keeps its name
        elif original_helper_name == "__extracted_func" or (
            class_context and re.fullmatch(r"__extracted_func(?:_\d+)?", original_helper_name)
        ):
            proposal.extracted_function.name = self._allocate_helper_name(
                proposal.file_path,
                class_context=class_context,
                related_paths=list(replacements_by_file),
            )
        elif proposal.insert_into_class and not original_helper_name.startswith("_"):
            # Preserve user-provided helper names but keep them non-public inside classes
            proposal.extracted_function.name = f"_{original_helper_name}"
        final_func_name = proposal.extracted_function.name
        receiver_name = proposal.method_param_name or (
            "cls" if proposal.method_kind == "classmethod" else "self"
        )
        original_parameters = [arg.arg for arg in proposal.extracted_function.args.args]
        receiver_parameter_index = (
            original_parameters.index(receiver_name)
            if receiver_name in original_parameters
            else None
        )

        # Process each file
        modified_files: Dict[str, str] = {}

        for file_path, replacements in replacements_by_file.items():
            lines = list(self._source_lines(file_path))

            # Sort replacements by line number (reverse order)
            replacements = sorted(replacements, key=lambda r: r.line_range[0], reverse=True)

            ascending = sorted(replacements, key=lambda replacement: replacement.line_range)
            if any(
                left.line_range[1] >= right.line_range[0]
                for left, right in zip(ascending, ascending[1:])
            ):
                raise ValueError(f"Overlapping replacements: {file_path}")
            # Apply each replacement (reverse sorted prevents earlier line shifts)
            for repl in replacements:
                start_line, end_line = repl.line_range
                replacement_node = copy.deepcopy(repl.node)
                class_name = repl.class_name

                if not 1 <= start_line <= end_line <= len(lines):
                    raise ValueError(
                        f"Invalid replacement range {start_line}-{end_line}: {file_path}"
                    )

                # Rewrite call sites for method extraction
                if proposal.insert_into_class and class_name:
                    replacement_node = self._rewrite_call_for_method(
                        replacement_node,
                        original_helper_name,
                        final_func_name,
                        repl.method_kind or proposal.method_kind,
                        repl.implicit_param or proposal.method_param_name,
                        class_name,
                        receiver_parameter_index,
                        len(original_parameters),
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

                replacement_code = self._render(replacement_node)
                code_lines = replacement_code.split("\n")
                indent = self._get_indent(lines[start_line - 1])

                # Build indented replacement block: indent ALL non-empty lines consistently
                replacement_lines: List[str] = []
                for line in code_lines:
                    if line.strip():
                        replacement_lines.append(indent + line + "\n")
                    else:
                        replacement_lines.append("\n")

                # Record the true before/after for this call site before splicing.
                # A call to an existing function needs no naming, so it is not logged.
                if proposal.reused_function is None:
                    self._change_log.append(
                        AppliedChange(
                            helper=final_func_name,
                            path=file_path,
                            line=start_line,
                            before=textwrap.dedent(
                                "".join(lines[start_line - 1 : end_line])
                            ).rstrip("\n"),
                            after=replacement_code,
                        )
                    )
                # Splice into source
                lines[start_line - 1 : end_line] = replacement_lines

            # Insert helper into canonical file or import into others
            if proposal.reused_function is not None and file_path == proposal.file_path:
                pass  # the function the calls target is already defined here
            elif file_path == proposal.file_path:
                # Names an inferred annotation needs that the module does not bind.
                for module_name, name in proposal.required_imports:
                    required_line = f"from {module_name} import {name}\n"
                    if not any(required_line.strip() == ln.strip() for ln in lines):
                        lines.insert(self._find_import_position(lines), required_line)
                if proposal.insert_into_function:
                    fn_insert_info = self._find_function_insert_position_before_body_statements(
                        "".join(lines), proposal.insert_into_function
                    )
                    fn_code = self._render(proposal.extracted_function)
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
                    func_code = self._render(proposal.extracted_function)
                    method_lines = [line + "\n" for line in func_code.split("\n")]
                    insert_info = self._find_class_insert_position(
                        "".join(lines), proposal.insert_into_class
                    )
                    if insert_info is None:
                        raise RefactoringError(
                            f"Class {proposal.insert_into_class} is not a unique module-level "
                            f"class in {file_path}; cannot insert a method"
                        )
                    else:
                        insert_line_zero_based, method_indent = insert_info
                        indented_method: List[str] = [
                            reindent(line, method_indent) if line.strip() else line
                            for line in method_lines
                        ]
                        insert_at = insert_line_zero_based + 1
                        method_prefix: List[str] = []
                        if insert_at > 0 and lines[insert_at - 1].strip():
                            method_prefix.append("\n")
                        method_suffix: List[str] = []
                        if insert_at < len(lines) and lines[insert_at].strip():
                            method_suffix.append("\n")
                        lines[insert_at:insert_at] = method_prefix + indented_method + method_suffix
                else:
                    func_code = self._render(proposal.extracted_function)
                    func_lines = [line + "\n" for line in func_code.split("\n")]
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
            else:
                # Non-canonical file: insert import (module-level only)
                if not proposal.insert_into_class:
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
                    importer_in_package = is_package_dir(to_path.parent, pep420=False)
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

                    func_name = proposal.extracted_function.name
                    import_line = f"from {module_name} import {func_name}\n"
                    if not any(import_line.strip() == ln.strip() for ln in lines):
                        import_pos = self._find_import_position(lines)
                        lines.insert(import_pos, import_line)

            assembled = "".join(lines)
            if self.file_finisher is not None:
                assembled = self.file_finisher(file_path, assembled)
            modified_files[file_path] = assembled

        for path, content in modified_files.items():
            compile(content, path, "exec")
        if proposal.reused_function is not None:
            self._verify_reused_function_calls(modified_files, proposal.reused_function, proposal)
        else:
            self._verify_helper_call_arity(modified_files, final_func_name, proposal.method_kind)
        return modified_files

    @staticmethod
    def _verify_helper_call_arity(
        modified_files: Dict[str, str],
        helper_name: str,
        method_kind: Optional[Literal["instance", "classmethod", "staticmethod"]],
    ) -> None:
        """Fail loudly if any generated call cannot bind to the generated helper.

        Method conversion and receiver removal happen after the instantiation
        check, so this compares the rendered helper signature with every call
        that names it. Bound calls omit the receiver; static calls do not.
        """
        parameters: Optional[int] = None
        for source in modified_files.values():
            for node in ast.walk(parse_cached(source)):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == helper_name
                ):
                    parameters = len(node.args.posonlyargs) + len(node.args.args)
        if parameters is None:
            raise RefactoringError(f"Helper {helper_name} was not emitted")
        for path, source in modified_files.items():
            for node in ast.walk(parse_cached(source)):
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
