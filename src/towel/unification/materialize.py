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
signature. When the run's original project passes its type checker, every
prospective project is checked. Precise ordinary signatures are tried first;
generic candidates are tried before losing type information to Any. Every
fallback, including a bare helper, must also pass. Existing project errors
refuse the run before any output is created.
Reused functions keep their existing signatures; a type error declines the
reuse rather than weakening those signatures. The immutable byte plan is
applied transactionally by changes.py.
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
from typing import Dict, FrozenSet, Iterator, List, Optional, Set, Tuple
from .exceptions import ProjectScanLimitError, RefactoringError
from .insertion import reindent, relative_import_module
from .models import AppliedChange, MethodKind, RefactoringProposal, Replacement
from ..consumers import MAXIMUM_FILES, SKIPPED_DIRECTORIES
from ..project_layout import (
    ProjectLayout,
    find_project_root,
    package_chain,
    package_chain_name,
)
from towel.changes import StaleSource, ChangePlan
from ..source_text import read_source
from ..type_inference import TypeDiagnostic

from .reuse import ExistingFunctionReuse
from .annotation_wiring import HelperAnnotationWiring
from .insertion import InsertionPoints
from .placement import HelperPlacement
from .statement_facts import bindings_of, loaded_names


@dataclass(frozen=True)
class _HelperNaming:
    """The helper's name before and after allocation, and where its receiver sits."""

    original_name: str
    final_name: str
    receiver_parameter_index: Optional[int]
    parameter_count: int


@dataclass(frozen=True)
class _Verified:
    """A variant the project accepted, with the files it would write."""

    files: Dict[str, str]


@dataclass(frozen=True)
class _Rejection:
    """The errors a checked variant introduced, and where its helper was rendered."""

    errors: Tuple[TypeDiagnostic, ...]
    helper_path: str
    helper_name: str
    helper_module: str

    def confined_to_helper(self) -> bool:
        """Whether every error lies within the helper's own definition.

        Asked of the all-``Any`` helper, this decides whether the unannotated one
        is worth a project check. To a caller the two are the same function: every
        parameter accepts anything and the result constrains nothing. They differ
        only on the helper's own lines, where a project may forbid explicit
        ``Any`` or leave an unannotated body unchecked. An error anywhere else
        survives the change, so the project would reject that helper too. An
        error the checker did not locate is taken to lie inside, which costs a
        check rather than a refactoring.
        """
        spans = [
            (
                min([node.lineno] + [decorator.lineno for decorator in node.decorator_list]),
                node.end_lineno or node.lineno,
            )
            for node in ast.walk(ast.parse(self.helper_module))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == self.helper_name
        ]
        return all(
            error.line is None
            or os.path.realpath(error.path) == os.path.realpath(self.helper_path)
            and any(first <= error.line <= last for first, last in spans)
            for error in self.errors
        )


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
                raise StaleSource(f"Source changed during planning: {path}")
        return ChangePlan.from_sources(before, after)

    def apply_refactoring_multi_file(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Render without writing or mutating caller-owned proposal ASTs.

        The first application establishes the run's complete original type
        baseline before inference or materialization. Later applications keep
        that policy until ``begin_refactoring_run`` starts another run.
        """
        for path, digest in proposal.source_digests:
            if hashlib.sha256(read_source(path).encode("utf-8")).hexdigest() != digest:
                raise StaleSource(f"Stale proposal; analyze again: {path}")
        self._ensure_type_checking(
            [
                *self._analysis_paths,
                proposal.file_path,
                *(rep.file_path or proposal.file_path for rep in proposal.replacements),
            ]
        )
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
        check_types = self._active_type_oracle() is not None
        counters = dict(self._helper_name_counters)
        rejection: Optional[_Rejection] = None
        for variant in self._annotation_variants(proposal, check_types):
            outcome = self._attempt(variant, counters, check_types)
            if isinstance(outcome, _Verified):
                return outcome.files
            rejection = outcome
        if (
            rejection is not None
            and rejection.confined_to_helper()
            and check_types
            and proposal.reused_function is None
            and self._helper_has_annotations(proposal)
        ):
            outcome = self._attempt(self._without_annotations(proposal), counters, check_types)
            if isinstance(outcome, _Verified):
                return outcome.files
        if proposal.reused_function is not None:
            raise RefactoringError("Reusing the existing function introduces project type errors")
        raise RefactoringError("Every helper annotation variant introduces project type errors")

    def _attempt(
        self, variant: RefactoringProposal, counters: Dict[str, int], check_types: bool
    ) -> "_Verified | _Rejection":
        """The files with ``variant`` applied, or why the project rejects them."""
        mark = len(self._change_log)
        # Each attempt allocates the helper's name; restore the counters so
        # every attempt gets the same name and none is consumed by a retry.
        self._helper_name_counters = dict(counters)
        # Naming mutates the rendered helper. Keep the lazy generator's
        # source proposal intact so later variants still retarget calls
        # from the original helper name.
        rendered = copy.deepcopy(variant)
        try:
            files = self._materialize_once(rendered)
            errors = self._new_type_errors(files) if check_types else ()
        except Exception:
            del self._change_log[mark:]
            raise
        if not errors:
            return _Verified(files)
        del self._change_log[mark:]
        return _Rejection(
            errors, rendered.file_path, rendered.extracted_function.name, files[rendered.file_path]
        )

    def _annotation_variants(
        self, proposal: RefactoringProposal, check_types: bool
    ) -> Iterator[RefactoringProposal]:
        """Keep a precise ordinary signature; try generics before losing annotations.

        Generation is lazy: successful concrete signatures need no additional
        checker probes. An ordinary signature containing Any is already lossy,
        so a parametric signature takes precedence when one can be verified.
        """
        if not check_types or proposal.reused_function is not None:
            yield proposal
            return
        lossy = self._helper_uses_any(proposal)
        if lossy:
            yield from self._generic_helper_variants(proposal)
        yield proposal
        if not lossy:
            yield from self._generic_helper_variants(proposal)
        if self._helper_has_annotations(proposal):
            # The unannotated helper follows only when it could help; see ``_Rejection``.
            yield self._with_every_annotation_any(proposal)

    def _materialize_once(self, proposal: RefactoringProposal) -> Dict[str, str]:
        """Render one proposal into modified sources (see ``_materialize_refactoring``)."""
        if proposal.helper_type_declarations and (
            proposal.reused_function is not None or proposal.insert_into_function is not None
        ):
            raise RefactoringError("Type declarations require a fresh module or class helper")
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
        declaration_names = self._type_declaration_names(proposal)
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
            taken = declaration_names | self._helper_names_in_project(proposal.file_path)
            while proposal.extracted_function.name in taken:
                proposal.extracted_function.name = self._allocate_helper_name(
                    proposal.file_path, class_context=class_context, related_paths=related_paths
                )
        elif proposal.insert_into_class and not original_name.startswith("_"):
            proposal.extracted_function.name = f"_{original_name}"
        if proposal.extracted_function.name in declaration_names:
            raise RefactoringError("The helper name conflicts with its type declarations")
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

    def _helper_names_in_project(self, file_path: str) -> FrozenSet[str]:
        """Every helper-shaped identifier the project around ``file_path`` already spells.

        Names are allocated against the files under analysis, and a file
        outside them can still own one: a subclass in another package that
        defines ``_extracted_func_0`` overrides a helper of that name placed in
        its base, and every call its instances make is hijacked. So the whole
        project is read, once per engine, for identifiers of the helper's
        shape, in the same directories the consumer scan reads. The project is
        found from the file's original location, since an output directory is
        only a copy of part of it.
        """
        root = find_project_root(Path(self._origin_of(file_path)))
        key = str(root)
        names = self._project_helper_names.get(key)
        if names is None:
            names = self._project_helper_names[key] = _helper_shaped_identifiers(root)
        return names

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
        for module_name, name in proposal.type_checking_imports:
            self._ensure_type_checking_import(lines, module_name, name)
        if proposal.insert_into_function:
            self._insert_helper_into_function(proposal, lines)
        elif proposal.insert_into_class:
            if proposal.helper_type_declarations:
                self._insert_method_type_declarations(proposal, lines)
            self._insert_helper_into_class(proposal, file_path, lines)
        else:
            self._insert_helper_at_module_level(proposal, lines)

    def _insert_helper_into_function(self, proposal: RefactoringProposal, lines: List[str]) -> None:
        if proposal.insert_into_function is None:
            raise RefactoringError("A function-hosted helper needs its host's name")
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
        if proposal.insert_into_class is None:
            raise RefactoringError("A class-hosted helper needs its host's name")
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
        node: ast.AST = proposal.extracted_function
        dependencies = self._placeable_dependencies(
            "".join(lines), self._annotation_names(proposal.extracted_function)
        )
        if proposal.helper_type_declarations:
            node = ast.Module(
                body=[*proposal.helper_type_declarations, proposal.extracted_function],
                type_ignores=[],
            )
            dependencies |= self._type_declaration_dependencies(proposal)
        func_lines = [line + "\n" for line in self._render(node).split("\n")]
        insert_line = self._find_insert_position(lines, dependencies)
        if proposal.helper_type_declarations:
            insert_line = self._type_declaration_position(lines, dependencies, insert_line)
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

    def _placeable_dependencies(self, source: str, names: Set[str]) -> Set[str]:
        """The annotation names the helper may be placed after; refuse when one it needs is not.

        A helper placed after a definition its annotations name can spell it
        bare, but only when nothing before that definition runs code that
        could call the helper (``placeable_after``): ``Y = f()`` above
        ``class Late`` calls the helper before a helper below ``Late`` exists.
        Where annotations are never evaluated (``from __future__ import
        annotations``) such a name needs no ordering at all; elsewhere a bare
        annotation read before its definition is a ``NameError`` at import,
        and the proposal is declined.
        """
        # The names ``_find_insert_position`` would move the helper past.
        module_names: Set[str] = set()
        for statement in self._parse_source(source).body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_names.add(statement.name)
            elif isinstance(statement, ast.Assign):
                module_names.update(t.id for t in statement.targets if isinstance(t, ast.Name))
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                module_names.add(statement.target.id)
        ordered = names & module_names
        blocked = ordered - self.placeable_after(source)
        if blocked and not _postpones_annotations(source):
            raise RefactoringError(
                f"The helper's annotations name {sorted(blocked)}, defined after code that"
                " could call the helper"
            )
        return ordered - blocked

    def _insert_method_type_declarations(
        self, proposal: RefactoringProposal, lines: List[str]
    ) -> None:
        """Keep fresh method binders at module scope, before the host class."""
        classes = [
            node
            for node in self._parse_source("".join(lines)).body
            if isinstance(node, ast.ClassDef) and node.name == proposal.insert_into_class
        ]
        if len(classes) != 1:
            raise RefactoringError("Type declarations need one module-level host class")
        owner = classes[0]
        class_start = min([owner.lineno, *(item.lineno for item in owner.decorator_list)]) - 1
        dependencies = self._type_declaration_dependencies(proposal)
        position = self._type_declaration_position(
            lines, dependencies, self._find_insert_position(lines, dependencies)
        )
        if position > class_start:
            raise RefactoringError("A method type declaration depends on a binding after its host")
        module = ast.Module(body=list(proposal.helper_type_declarations), type_ignores=[])
        rendered = [line + "\n" for line in self._render(module).split("\n")]
        lines[position:position] = _padded(lines, position, rendered)

    @staticmethod
    def _type_declaration_dependencies(proposal: RefactoringProposal) -> Set[str]:
        return {
            name
            for statement in proposal.helper_type_declarations
            for name in loaded_names(statement)
        } - Materialization._type_declaration_names(proposal)

    @staticmethod
    def _type_declaration_names(proposal: RefactoringProposal) -> Set[str]:
        return {
            name
            for statement in proposal.helper_type_declarations
            for name in bindings_of(statement, into_nested_scopes=False)
        }

    def _type_declaration_position(
        self, lines: List[str], dependencies: Set[str], insert_line: int
    ) -> int:
        """Keep eager declaration dependencies available before the helper can be called."""
        body = self._parse_source("".join(lines)).body
        movable = True
        for statement in body:
            movable = movable and self._is_definition_like(statement)
            if not dependencies.intersection(bindings_of(statement, into_nested_scopes=False)):
                continue
            if not movable or isinstance(statement, (ast.If, ast.Try)):
                raise RefactoringError(
                    "A helper type declaration depends on an unavailable binding"
                )
            insert_line = max(insert_line, statement.end_lineno or statement.lineno)
        return insert_line

    def _insert_helper_import(
        self, proposal: RefactoringProposal, file_path: str, lines: List[str]
    ) -> None:
        """Import the module-level helper into a file whose call sites need it."""
        from_path = Path(proposal.file_path)
        to_path = Path(file_path)
        # An absolute name is read from the project the code belongs to, not
        # from wherever a run happens to be writing it. An output directory is
        # a staging area: naming a module after it states a fact about the
        # scratch path, which is wrong for the checker, since it checks the
        # copy under the original project's names, and wrong again for the
        # reader, whose import breaks as soon as the output is adopted into
        # the place it was meant for. A relative import says only that the two
        # modules share a package, which is true in either tree.
        origin_from = Path(self._origin_of(str(from_path)))
        origin_to = Path(self._origin_of(str(to_path)))
        common_dir = Path(os.path.commonpath([str(origin_from), str(origin_to)]))
        layout = ProjectLayout.discover(
            common_dir,
            prefer_absolute_imports=self.prefer_absolute_imports,
            pep420_namespace_packages=self.pep420_namespace_packages,
        )
        absolute = _corroborated_module_name(layout, origin_from)
        # A relative import states only that the two modules share a package,
        # which holds wherever the package is installed. None when the
        # importer is in no package or the helper lies outside its top one.
        relative = relative_import_module(from_path, to_path)
        # Prefer an absolute import only when the layout is anchored by real
        # packaging metadata and the name is corroborated; otherwise a
        # relative import, which encodes only the intrinsic same-package
        # relationship and matches the surrounding intra-package style.
        if absolute and layout.prefer_absolute_imports and layout.metadata_root:
            module_name = absolute
        elif relative is not None:
            module_name = relative
        elif absolute:
            module_name = absolute
        else:
            raise RefactoringError(
                f"No import of {origin_from} from {origin_to} can be shown to resolve:"
                " the packaging metadata and the package markers name it differently"
            )
        self._ensure_import(lines, module_name, proposal.extracted_function.name)

    def _ensure_import(self, lines: List[str], module_name: str, name: str) -> None:
        """Add ``from module_name import name`` at the import position unless a line already says so."""
        import_line = f"from {module_name} import {name}\n"
        if not any(import_line.strip() == ln.strip() for ln in lines):
            lines.insert(self._find_import_position(lines), import_line)

    def _ensure_type_checking_import(self, lines: List[str], module_name: str, name: str) -> None:
        """State ``from module_name import name`` where only a checker will read it.

        The name is wanted by an annotation and never at run time, so importing
        it under ``TYPE_CHECKING`` keeps the module's runtime imports as they
        were and cannot close an import cycle -- which matters here, because
        the extraction has often just made that module import this one. An
        existing guard is extended rather than a second one written.
        """
        wanted = f"from {module_name} import {name}"
        if any(wanted == line.strip() for line in lines):
            return
        self._ensure_import(lines, "typing", "TYPE_CHECKING")
        for index, line in enumerate(lines):
            # Only a guard at module level: one indented inside a function or
            # class would take the import out of the scope the annotation
            # reads it in.
            if line.rstrip("\n") in {"if TYPE_CHECKING:", "if typing.TYPE_CHECKING:"}:
                lines.insert(index + 1, f"    {wanted}\n")
                return
        at = self._find_import_position(lines)
        lines[at:at] = ["\n", "if TYPE_CHECKING:\n", f"    {wanted}\n"]

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


def _postpones_annotations(source: str) -> bool:
    """Whether the module is written under ``from __future__ import annotations``."""
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return False
    return any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in body
    )


_HELPER_SHAPED = re.compile(r"(?<!\w)_{1,2}extracted_func(?:_\d+)?(?!\w)")


def _helper_shaped_identifiers(root: Path) -> FrozenSet[str]:
    """The helper-shaped names that sources under ``root`` could make override a helper.

    A helper is reached as a method or as a module attribute, so another file
    can take its place only by giving a class a member of that name or by
    assigning the attribute: a ``def`` or assignment in a class body, an
    attribute store anywhere, or the name as a string given to ``setattr``,
    stored by subscript into a namespace, or keyed in a ``type(...)``
    namespace. A mere call or mention takes nothing. A file that does not parse cannot be told
    apart, so every helper-shaped word in it counts. Past the consumer scan's
    limit the project cannot be read whole, and the run stops, as a typed
    run's consumer scan does, rather than choose a name some unread file may
    own.
    """
    found: Set[str] = set()
    count = 0
    for parent, directories, files in os.walk(root, onerror=lambda _: None):
        directories[:] = [name for name in directories if name not in SKIPPED_DIRECTORIES]
        for name in files:
            if not name.endswith((".py", ".pyi")):
                continue
            count += 1
            if count > MAXIMUM_FILES:
                raise ProjectScanLimitError(
                    f"{root} holds more than {MAXIMUM_FILES} Python files, so which helper"
                    " names its sources already use cannot be established. Towel reads"
                    " the project from the nearest directory with a pyproject.toml,"
                    " setup.cfg or setup.py; give the code one, or move it out of the"
                    " larger tree"
                )
            try:
                text = Path(parent, name).read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue
            words = set(_HELPER_SHAPED.findall(text))
            if words:
                found.update(_overriding_names(text, words))
    return frozenset(found)


def _overriding_names(text: str, words: Set[str]) -> Set[str]:
    """Of the helper-shaped ``words`` in ``text``, those it defines where a helper could be."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return words
    defined: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            pending: List[ast.AST] = list(node.body)
            while pending:
                member = pending.pop()
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(member.name)
                    continue
                if isinstance(member, ast.Name) and isinstance(member.ctx, ast.Store):
                    defined.add(member.id)
                pending.extend(ast.iter_child_nodes(member))
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.attr)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            defined.update(_string_constants([node.slice]))  # namespace["name"] = ...
        elif isinstance(node, ast.Call):
            callee = node.func
            called = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
            if called in {"setattr", "__setattr__"} and len(node.args) >= 2:
                defined.update(_string_constants([node.args[1]]))
            elif called == "type" and len(node.args) == 3 and isinstance(node.args[2], ast.Dict):
                defined.update(_string_constants([k for k in node.args[2].keys if k]))
    return defined & words


def _string_constants(nodes: List[ast.expr]) -> Set[str]:
    return {
        node.value
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _corroborated_module_name(layout: ProjectLayout, path: Path) -> Optional[str]:
    """The absolute module name of ``path``, when two independent derivations agree on it.

    The layout readers reimplement five build backends' package discovery,
    and a wrong answer from them is an import that names a module the
    installed project does not have: ``src.foo.a`` for a ``setup.cfg`` src
    layout they do not read, ``foo.src.foo.a`` for a project directory named
    like its package. The checker cannot catch it, since with no project
    configuration it names modules from the same root. The name the
    ``__init__`` markers imply is a second derivation that shares none of
    that machinery, and a name both give is the one used. When they differ
    the name is unknown, and a caller that has no relative import to fall
    back on declines rather than guess.

    A module in no regular package, in a project with no packaging metadata,
    has no second derivation: its only name is its path from the import
    root, which for such a project is the directory Towel was pointed at.
    That assumption is the documented one, and it is kept there alone.
    """
    declared = layout.module_name_for(path)
    if declared is None or declared == package_chain_name(path):
        return declared
    if not layout.metadata_root and not package_chain(path.resolve()):
        return declared
    return None
