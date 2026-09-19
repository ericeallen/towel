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

"""Wiring the annotation rules into a proposal, and verifying the result.

The engine asks annotations.py to copy what the call sites declare and to
infer the rest through the project's type checker, decides whether the
generated code is to be type-checked, produces the fallback variants (every
annotation Any, then none), and compares the checker's messages before and
after a change so only new errors count.
"""

from __future__ import annotations

import ast
from collections import Counter
import copy
import dataclasses

from typing import Dict, List, Optional, Set
from .annotations import (
    ApplySite,
    CallSite,
    annotate_helper,
    call_in_statement,
    complete_with_any,
    infer_missing_annotations,
    respell_bare,
    sites_use_annotations,
    typing_imports_needed,
)
from .exceptions import RefactoringError
from .models import FunctionNode, RefactoringProposal, span_contains
from ..diagnostics import TYPES
from ..type_inference import CheckFailure, CheckResult

from .engine_state import EngineState
from ..source_text import read_source, source_lines, try_read_source
from .function_index import FunctionIndex


class HelperAnnotationWiring(EngineState):
    """Helper AnnotationWiring methods of the engine; see the module docstring."""

    def _with_helper_annotations(
        self, proposal: RefactoringProposal, functions: FunctionIndex
    ) -> RefactoringProposal:
        """The proposal with its helper annotated from what the call sites declare.

        Runs after every verification, since annotations play no part in the
        instantiation check, and after clustering, which compares helper
        bodies structurally.
        """
        sites: List[CallSite] = []
        for replacement in proposal.replacements:
            file_path = replacement.file_path or proposal.file_path
            call = call_in_statement(replacement.node, proposal.extracted_function.name)
            function = functions.innermost_at(file_path, replacement.line_range)
            module = function.scope_analyzer.analyzed_tree if function is not None else None
            if call is None or function is None or not isinstance(module, ast.Module):
                return proposal
            sites.append(
                CallSite(
                    statement=replacement.node,
                    call=call,
                    function=function.node,
                    module=module,
                    file_path=file_path,
                )
            )
        annotated = annotate_helper(
            proposal.extracted_function, sites, proposal.file_path, proposal.return_variables
        )
        return dataclasses.replace(
            proposal,
            extracted_function=annotated,
            wants_type_inference=sites_use_annotations(sites),
        )

    def _infer_helper_annotations(self, proposal: RefactoringProposal) -> None:
        """Finish the helper's annotations in place when the proposal is applied.

        The type inferrer, when there is one, fills what the copied
        annotations could not; then a helper that carries any annotation gets
        ``Any`` on whatever is still bare, so its signature is complete. Runs
        once per applied proposal, on the files as they stand, so the cost is
        one incremental type-check per application rather than one per
        candidate.
        """
        if not proposal.wants_type_inference or proposal.reused_function is not None:
            return
        module_level = proposal.insert_into_class is None and proposal.insert_into_function is None
        host_source = self._read_source(proposal.file_path)
        bare_ok = self.placeable_after(host_source) if module_level and host_source else set()
        host = self._parsed_host(proposal.file_path)
        if self.type_oracle is None:
            respelled = respell_bare(proposal.extracted_function, host, bare_ok)
            completed = complete_with_any(respelled, host)
            proposal.extracted_function = completed.helper
            proposal.required_imports = completed.required_imports
            return
        sites: List[ApplySite] = []
        sources: Dict[str, str] = {}
        for replacement in proposal.replacements:
            file_path = replacement.file_path or proposal.file_path
            call = call_in_statement(replacement.node, proposal.extracted_function.name)
            if call is None:
                return
            source = sources.get(file_path)
            if source is None:
                source = read_source(file_path)
                sources[file_path] = source
            lines = source_lines(source)
            start_line, end_line = replacement.line_range
            if not 1 <= start_line <= len(lines):
                return
            sites.append(
                ApplySite(
                    file_path=file_path,
                    source=source,
                    start_line=start_line,
                    end_line=end_line,
                    indent=self._get_indent(lines[start_line - 1]),
                    statement=replacement.node,
                    call=call,
                    declared_return=self._declared_return_at(source, start_line),
                )
            )
        inferred = infer_missing_annotations(
            respell_bare(proposal.extracted_function, host, bare_ok),
            sites,
            proposal.file_path,
            proposal.return_variables,
            self.type_oracle,
            bare_ok,
        )
        completed = complete_with_any(respell_bare(inferred.helper, host, bare_ok), host)
        proposal.extracted_function = completed.helper
        proposal.required_imports = tuple(
            dict.fromkeys(inferred.required_imports + completed.required_imports)
        )

    @staticmethod
    def _annotation_names(helper: ast.FunctionDef) -> Set[str]:
        """Names the helper's unquoted annotations refer to."""
        names: Set[str] = set()
        annotations = [arg.annotation for arg in helper.args.posonlyargs + helper.args.args] + [
            helper.returns
        ]
        for annotation in annotations:
            if annotation is not None and not isinstance(annotation, ast.Constant):
                names |= {n.id for n in ast.walk(annotation) if isinstance(n, ast.Name)}
        return names

    @staticmethod
    def _read_source(file_path: str) -> Optional[str]:
        return try_read_source(file_path)

    @staticmethod
    def _declared_return_at(source: str, line: int) -> Optional[ast.expr]:
        """The return annotation of the innermost function containing ``line``."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        innermost: Optional[FunctionNode] = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and span_contains(node, (line, line))
                and (innermost is None or node.lineno > innermost.lineno)
            ):
                innermost = node
        return innermost.returns if innermost is not None else None

    def _parsed_host(self, file_path: str) -> Optional[ast.Module]:
        source = try_read_source(file_path)
        if source is None:
            return None
        try:
            return self._parse_source(source)
        except SyntaxError:
            return None

    def _checks_project_types(self, proposal: RefactoringProposal) -> bool:
        """Check reused calls and annotated new helpers when an oracle is present."""
        if self.type_oracle is None:
            return False
        if proposal.reused_function is not None:
            return True
        helper = proposal.extracted_function
        return helper.returns is not None or any(
            arg.annotation is not None for arg in helper.args.posonlyargs + helper.args.args
        )

    def _with_every_annotation_any(self, proposal: RefactoringProposal) -> RefactoringProposal:
        """The proposal with every helper annotation replaced by ``Any``.

        Only the helper is copied: the replacements are shared with the
        proposal, and materialization copies each one before touching it.
        """
        helper = copy.deepcopy(proposal.extracted_function)
        variant = dataclasses.replace(proposal, extracted_function=helper)
        for arg in helper.args.posonlyargs + helper.args.args:
            arg.annotation = ast.Name(id="Any", ctx=ast.Load())
        helper.returns = ast.Name(id="Any", ctx=ast.Load())
        variant.required_imports = typing_imports_needed(
            helper, self._parsed_host(variant.file_path)
        )
        return variant

    @staticmethod
    def _without_annotations(proposal: RefactoringProposal) -> RefactoringProposal:
        """The proposal with the helper unannotated; see ``_with_every_annotation_any``."""
        helper = copy.deepcopy(proposal.extracted_function)
        variant = dataclasses.replace(proposal, extracted_function=helper)
        for arg in helper.args.posonlyargs + helper.args.args:
            arg.annotation = None
        helper.returns = None
        variant.required_imports = ()
        return variant

    def _original_type_errors(self, modified_files: Dict[str, str]) -> CheckResult:
        """Check the complete original project once for every prospective variant."""
        if self.type_oracle is None:
            raise RefactoringError("Type checking was requested without a type oracle")
        originals: Dict[str, str] = {}
        for path in modified_files:
            source = self._read_source(path)
            if source is None:
                return CheckFailure(f"Cannot read original source: {path}")
            originals[path] = source
        return self.type_oracle.check_project(originals)

    def _introduces_type_errors(
        self, modified_files: Dict[str, str], errors_before: CheckResult
    ) -> bool:
        """Compare complete prospective and original projects, including unchanged consumers."""
        if self.type_oracle is None:
            raise RefactoringError("Type checking was requested without a type oracle")
        if isinstance(errors_before, CheckFailure):
            raise RefactoringError(f"Original project type check failed: {errors_before.reason}")
        after = self.type_oracle.check_project(modified_files)
        if isinstance(after, CheckFailure):
            raise RefactoringError(f"Prospective project type check failed: {after.reason}")
        new = Counter(after.errors) - Counter(errors_before.errors)
        for diagnostic, count in new.items():
            TYPES.debug("new error x%d in %s: %s", count, diagnostic.path, diagnostic.message)
        return bool(new)
