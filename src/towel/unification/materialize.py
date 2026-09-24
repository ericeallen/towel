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
from typing import Dict, List, Optional, Set
from .class_private import is_class_private, mangled, mangling_classes, mangling_prefix
from .engine_state import HelperNameClaims
from .exceptions import ProjectScanLimitError, RefactoringError, UntypeableExtraction
from .import_graph import ImportTimeCode, fails_run_by_path, runs_as_script
from .insertion import reindent
from .models import (
    AppliedChange,
    MethodKind,
    RefactoringProposal,
    Replacement,
    is_generated_helper_name,
)
from ..consumers import MAXIMUM_FILES, SKIPPED_DIRECTORIES
from ..project_layout import find_project_root
from towel.changes import StaleSource, ChangePlan
from ..source_text import read_source

from .reuse import ExistingFunctionReuse
from .annotation_ladder import Hearing, Rejection, Verified
from .annotation_wiring import HelperAnnotationWiring
from .insertion import InsertionPoints
from .placement import HelperPlacement, method_helper_position
from .statement_facts import bindings_of, loaded_names


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
        hearing = Hearing(self._judge_for(proposal))
        for variant in self._annotation_ladder(proposal, check_types, hearing):
            outcome = self._attempt(variant, counters, check_types)
            if isinstance(outcome, Verified):
                return outcome.files
            hearing.refused(variant, outcome)
        settled = hearing.settled_by()
        if settled is not None:
            raise UntypeableExtraction(settled.reason, settled.detail)
        if proposal.reused_function is not None:
            raise RefactoringError("Reusing the existing function introduces project type errors")
        raise RefactoringError("Every helper annotation variant introduces project type errors")

    def _attempt(
        self, variant: RefactoringProposal, counters: Dict[str, int], check_types: bool
    ) -> "Verified | Rejection":
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
            errors = (
                self._project_errors(files, rendered.extracted_function.name) if check_types else ()
            )
        except Exception:
            del self._change_log[mark:]
            raise
        if not errors:
            return Verified(files)
        del self._change_log[mark:]
        return Rejection(
            errors,
            rendered.file_path,
            rendered.extracted_function.name,
            files[rendered.file_path],
            files,
        )

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

        if proposal.insert_into_class is not None:
            self._refuse_calls_outside_the_host(proposal)
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
        so it stays unique across them, and against the names the project's
        other sources already define where the helper would live
        (``HelperNameClaims``). A method helper is class-private
        (:meth:`_method_helper_name`). A module-level helper called from
        inside a class body cannot start with two underscores, which the body
        would mangle into another name. A user-provided name is kept, made
        class-private for a method helper; a reused function keeps the name the
        calls already use.
        """
        original_name = proposal.extracted_function.name
        declaration_names = self._type_declaration_names(proposal)
        called_in_a_class = any(
            replacement.class_name is not None for replacement in proposal.replacements
        )
        if proposal.reused_function is not None:
            pass  # the calls already name an existing function; it keeps its name
        elif proposal.insert_into_class is not None:
            proposal.extracted_function.name = self._method_helper_name(
                proposal, proposal.insert_into_class, related_paths, declaration_names
            )
        elif original_name == "__extracted_func" or (
            called_in_a_class and re.fullmatch(r"__extracted_func(?:_\d+)?", original_name)
        ):
            prefix = "_extracted_func" if called_in_a_class else "__extracted_func"
            taken = declaration_names | self._helper_names_in_project(proposal.file_path).namespace
            proposal.extracted_function.name = self._allocate_helper_name(
                proposal.file_path, prefix=prefix, related_paths=related_paths
            )
            while proposal.extracted_function.name in taken:
                proposal.extracted_function.name = self._allocate_helper_name(
                    proposal.file_path, prefix=prefix, related_paths=related_paths
                )
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

    def _method_helper_name(
        self,
        proposal: RefactoringProposal,
        host: str,
        related_paths: List[str],
        declaration_names: Set[str],
    ) -> str:
        """A class-private name for a method helper of ``host``: ``__extracted_func_N``.

        ``host`` stores it as ``_Host__extracted_func_N`` and its methods call
        it as ``self.__extracted_func_N()``, which the compiler rewrites the
        same way, so no subclass, in the project or outside it, can override
        it or collide with it: a subclass's own ``__extracted_func_N`` is
        stored under the subclass's name. The name must be free only in that
        stored form, among the members the project's sources already define
        (``HelperNameClaims.members``); a same-named class elsewhere that
        defines the unmangled name claims it too, because Python mangles by
        name and not by class. A class named only with underscores mangles
        nothing, so pair evaluation never gives it a method helper.
        """
        prefix = mangling_prefix(host)
        if prefix is None:
            raise RefactoringError(
                f"Class {host} is named only with underscores, so no name in it is private"
                " and it cannot take a method helper"
            )
        original = proposal.extracted_function.name
        if not is_generated_helper_name(original):
            # A caller's own name, made class-private as every method helper is.
            private = original if is_class_private(original) else "__" + original.lstrip("_")
            if not is_class_private(private):
                raise RefactoringError(f"The method helper name {original!r} cannot be private")
            return private
        claimed = self._helper_names_in_project(proposal.file_path).members
        while True:
            name = self._allocate_helper_name(
                proposal.file_path, prefix="__extracted_func", related_paths=related_paths
            )
            if name not in declaration_names and prefix + name not in claimed:
                return name

    def _refuse_calls_outside_the_host(self, proposal: RefactoringProposal) -> None:
        """Refuse a method helper any of whose calls lies outside its own class's body.

        ``self.__extracted_func_0()`` reaches ``_A__extracted_func_0`` only when
        it is written in ``A``'s body, not in a class nested there nor in
        another module; anywhere else the compiler rewrites it to another
        name, or none. Pair evaluation calls a method helper only from methods
        of its class, so a site elsewhere is a proposal built by other means,
        whose output would raise ``AttributeError`` where it ran.
        """
        host = next(
            (
                node
                for node in self._parse_source("".join(self._source_lines(proposal.file_path))).body
                if isinstance(node, ast.ClassDef) and node.name == proposal.insert_into_class
            ),
            None,
        )
        for replacement in proposal.replacements:
            start, end = replacement.line_range
            outside = (
                host is None
                or (replacement.file_path or proposal.file_path) != proposal.file_path
                or not (host.body[0].lineno <= start and end <= (host.end_lineno or host.lineno))
                or any(
                    node is not host
                    and isinstance(node, ast.ClassDef)
                    and node.lineno <= start
                    and end <= (node.end_lineno or node.lineno)
                    for node in ast.walk(host)
                )
            )
            if outside:
                raise RefactoringError(
                    f"A method helper of {proposal.insert_into_class} is class-private and can be"
                    f" called only from that class's own body, not from line {start}"
                )

    def _helper_names_in_project(self, file_path: str) -> HelperNameClaims:
        """What the project around ``file_path`` already defines under helper-shaped names.

        Names are allocated against the files under analysis, and a file
        outside them can still own one: an assignment ``lib._extracted_func_0
        = ...`` there would replace a module-level helper of that name, and a
        class member stored as ``_A__extracted_func_0`` would override a method
        helper of ``A``. So the whole project is read, once per engine, in the
        same directories the consumer scan reads. The project is found from
        the file's original location, since an output directory is only a copy
        of part of it.
        """
        root = find_project_root(Path(self._origin_of(file_path)))
        key = str(root)
        names = self._project_helper_names.get(key)
        if names is None:
            names = self._project_helper_names[key] = _claimed_helper_names(root)
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
            before = "".join(lines)
            self._insert_helper_import(proposal, file_path, lines)
            self._refuse_relative_import_in_a_script(
                before, "".join(lines), file_path, proposal.extracted_function.name
            )
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
        if (proposal.method_kind or "instance") == "instance":
            # Where the class's attributes keep the declarations they had.
            ordered = method_helper_position(
                "".join(lines),
                proposal.insert_into_class,
                proposal.extracted_function,
                proposal.method_param_name or "self",
            )
            insert_at = insert_at if ordered is None else ordered
        lines[insert_at:insert_at] = _padded(lines, insert_at, indented)

    def _insert_helper_at_module_level(
        self, proposal: RefactoringProposal, lines: List[str]
    ) -> None:
        node: ast.AST = proposal.extracted_function
        dependencies = self._placeable_dependencies(
            "".join(lines),
            self._annotation_names(proposal.extracted_function),
            Path(proposal.file_path),
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

    def _placeable_dependencies(self, source: str, names: Set[str], path: Path) -> Set[str]:
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
        blocked = ordered - self.placeable_after(source, path=path, cache=self.import_graph)
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
        code = ImportTimeCode("".join(lines))
        movable = True
        for statement, runs_code in zip(code.tree.body, code.statements()):
            movable = movable and not runs_code
            if not dependencies.intersection(bindings_of(statement, into_nested_scopes=False)):
                continue
            if not movable or isinstance(statement, (ast.If, ast.Try)):
                raise RefactoringError(
                    "A helper type declaration depends on an unavailable binding"
                )
            insert_line = max(insert_line, statement.end_lineno or statement.lineno)
        return insert_line

    def _refuse_relative_import_in_a_script(
        self, before: str, after: str, file_path: str, helper_name: str
    ) -> None:
        """Refuse a relative helper import in a module that still runs as a script by path.

        Run by its path, a module has no package, so ``from .host import
        helper`` raises there; pair evaluation admitted the borrower only for an
        absolute import its leading imports already make resolvable
        (``import_graph._breaks_run_by_path``). One whose leading imports are
        already relative fails by path anyway, at that import.
        """
        tree = self._parse_source(before)
        if not runs_as_script(before, tree, Path(file_path)) or fails_run_by_path(tree):
            return
        if any(
            isinstance(node, ast.ImportFrom)
            and node.level
            and any(alias.name == helper_name for alias in node.names)
            for node in self._parse_source(after).body
        ):
            raise RefactoringError(
                f"{file_path} runs as a script by its path, where the relative import of"
                f" {helper_name} could not resolve"
            )

    def _insert_helper_import(
        self, proposal: RefactoringProposal, file_path: str, lines: List[str]
    ) -> None:
        """Import the module-level helper into a file whose call sites need it.

        The import is spelled as the program's own imports show it works
        wherever the program runs (``ImportModel.spelling``; docs/DECISIONS.md,
        "Import names come from the program"): relatively between modules of
        one package, unless the importing file spells its own package
        absolutely, and absolutely across top-level packages only where the
        importing package already imports the other. It is never read from
        packaging metadata, nor from where a run happens to be writing: the
        model is read from the project the stage copies. Host selection
        admits a host only when every borrower has such a spelling, so one
        missing here is a proposal built by other means, refused rather than
        guessed at.
        """
        if not self.cross_module_helpers:
            # Pairing never forms such a proposal; one built by other means
            # would add a dependency between modules nobody asked for.
            raise RefactoringError(
                f"{file_path} would import {proposal.extracted_function.name} from"
                f" {proposal.file_path}: helpers are shared across modules only with"
                " cross_module_helpers (--cross-module)"
            )
        importer, provider = Path(file_path), Path(proposal.file_path)
        spelling = self.import_graph.program_for(provider).spelling(importer, provider)
        if spelling is None:
            raise RefactoringError(
                f"No import of {provider} from {importer} is known to work: the program's"
                " own imports show none"
            )
        self._ensure_import(lines, spelling.module, proposal.extracted_function.name)

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


_HELPER_SHAPED = re.compile(r"(?<!\w)\w*extracted_func(?:_\d+)?(?!\w)")
"""A word a helper name could be: a generated name, or one a class body mangled."""


def _claimed_helper_names(root: Path) -> HelperNameClaims:
    """The helper-shaped names that sources under ``root`` define where a helper could be.

    A module-level helper is reached as an attribute of its module, so another
    file takes its place only by assigning that attribute: an attribute store,
    or the name as a string given to ``setattr`` or stored by subscript into a
    namespace. A method helper is class-private and reached as ``_A__name``,
    so another file takes its place only by defining a member stored under
    that name, in a class body, by an attribute store, a ``setattr``, a
    subscript into a namespace, or a ``type(...)`` namespace; a subclass's
    ``_extracted_func_0``, which once had to be kept clear of, can no longer
    reach it. A mere call or mention takes nothing. A file that does not parse
    cannot be told apart, so every helper-shaped word in it counts, spelled
    as it stands and under every class's name. Past the consumer scan's limit
    the project cannot be read whole, and the run stops, as a typed run's
    consumer scan does, rather than choose a name some unread file may own.
    """
    namespace: Set[str] = set()
    members: Set[str] = set()
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
            if "extracted_func" not in text:
                continue
            claims = _claims_in(text)
            namespace |= claims.namespace
            members |= claims.members
    return HelperNameClaims(frozenset(namespace), frozenset(members))


def _claims_in(text: str) -> HelperNameClaims:
    """Of the helper-shaped names ``text`` defines, those it defines where a helper could be.

    Each is the name as stored: a class member or an attribute stored inside
    a class body is mangled with the innermost class whose body holds it.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _claims_of_unreadable_source(text)
    owners = mangling_classes(tree)
    namespace: Set[str] = set()
    members: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            members.update(_member_names(node))
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            owner = owners.get(node)
            namespace.add(mangled(node.attr, owner.name if owner is not None else None))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            namespace.update(_string_constants([node.slice]))  # namespace["name"] = ...
        elif isinstance(node, ast.Call):
            callee = node.func
            called = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
            if called in {"setattr", "__setattr__"} and len(node.args) >= 2:
                namespace.update(_string_constants([node.args[1]]))
            elif called == "type" and len(node.args) == 3 and isinstance(node.args[2], ast.Dict):
                members.update(_string_constants([k for k in node.args[2].keys if k]))
    shaped = {name for name in namespace | members if _HELPER_SHAPED.fullmatch(name)}
    return HelperNameClaims(
        frozenset(shaped & namespace), frozenset(shaped & (members | namespace))
    )


def _claims_of_unreadable_source(text: str) -> HelperNameClaims:
    """Every helper-shaped word of a file that does not parse, as it stands and in every class."""
    words = set(_HELPER_SHAPED.findall(text))
    classes = {match.lstrip("_") for match in re.findall(r"\bclass\s+(\w+)", text)}
    private = {mangled(word, owner) for word in words for owner in classes if owner}
    return HelperNameClaims(frozenset(words), frozenset(words | private))


def _member_names(node: ast.ClassDef) -> Set[str]:
    """Every name ``node``'s body binds, as the class stores it (``__x`` in ``A`` is ``_A__x``)."""
    defined: Set[str] = set()
    pending: List[ast.AST] = list(node.body)
    while pending:
        member = pending.pop()
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(mangled(member.name, node.name))
            continue
        if isinstance(member, ast.Name) and isinstance(member.ctx, ast.Store):
            defined.add(mangled(member.id, node.name))
        pending.extend(ast.iter_child_nodes(member))
    return defined


def _string_constants(nodes: List[ast.expr]) -> Set[str]:
    return {
        node.value
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
