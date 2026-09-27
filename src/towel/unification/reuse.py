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

"""Duplicates that are the whole body of a function.

A duplicate that is all a function does is extracted like any other, and
the function becomes a call of the new helper. It is never rewritten to call
another existing function that restates it instead: a call by name looks
that function up in its module every time, so ``mock.patch("mod.f1")``, or
any other rebinding of ``mod.f1``, would then change ``f2`` too (audit r06).
An earlier pass's generated helper may instead be reused when the proposed
call proves it computes precisely the new helper's result. Otherwise a
proposal that reduces it to a forwarder is declined. Simultaneous batches
can leave equivalent real helpers; merging their existing consumers is not
part of this local rewrite.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
from pathlib import Path

from typing import Dict, List, Optional, Sequence, Tuple
from .annotation_imports import words_of
from .block_comments import HelperComments, SiteComments
from .exceptions import RefactoringError
from .models import (
    FunctionNode,
    RefactoringProposal,
    Replacement,
    ReusedFunction,
    is_generated_helper_name,
)
from .visitors import body_without_docstring

from .engine_state import EngineState
from ..source_text import read_source
from .function_index import FunctionIndex
from .module_bindings import global_bindings


def _bare_names(expression: ast.expr) -> Optional[List[str]]:
    """The name ``x`` spells, or the names a tuple ``x, y`` of names spells; None for anything else."""
    if isinstance(expression, ast.Name):
        return [expression.id]
    if isinstance(expression, ast.Tuple) and all(
        isinstance(elt, ast.Name) for elt in expression.elts
    ):
        return [elt.id for elt in expression.elts if isinstance(elt, ast.Name)]
    return None


class ExistingFunctionReuse(EngineState):
    """ExistingFunctionReuse methods of the engine; see the module docstring."""

    @staticmethod
    def _positional_parameter_names(function: ast.FunctionDef) -> Optional[List[str]]:
        """The parameters a positional call binds, in order; None when some cannot be."""
        arguments = function.args
        if arguments.vararg or arguments.kwarg or arguments.kwonlyargs:
            return None
        return [arg.arg for arg in arguments.posonlyargs + arguments.args]

    @staticmethod
    def _unwrap_helper_call(statement: ast.AST, helper_name: str) -> Optional[ast.Call]:
        """The plain positional helper call inside a generated statement, if that is its shape."""
        if not isinstance(statement, (ast.Return, ast.Expr, ast.Assign)):
            return None
        call = statement.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == helper_name
            and not call.keywords
            and not any(isinstance(arg, ast.Starred) for arg in call.args)
        ):
            return call
        return None

    @staticmethod
    def _assigned_names(statement: ast.AST) -> Optional[List[str]]:
        """The names a generated ``x = helper()`` or ``x, y = helper()`` binds; None for other shapes."""
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            return None
        return _bare_names(statement.targets[0])

    @staticmethod
    def _returned_names(statement: ast.stmt) -> Optional[List[str]]:
        """The names a plain ``return x`` or ``return x, y`` yields; None for other shapes."""
        if not isinstance(statement, ast.Return) or statement.value is None:
            return None
        return _bare_names(statement.value)

    @classmethod
    def _return_positions(
        cls, assigned: Sequence[str], statement: ast.stmt
    ) -> Optional[Tuple[int, ...]]:
        """Where each name a plain ``return`` yields sits in the site's assignment, or None.

        None when the statement is not a plain return of exactly the assigned
        names; their order may differ, and the positions record how.
        """
        returned = cls._returned_names(statement)
        if (
            returned is None
            or sorted(returned) != sorted(assigned)
            or len(set(returned)) != len(returned)
        ):
            return None
        return tuple(assigned.index(name) for name in returned)

    def _site_is_whole_body(self, replacement: Replacement, function: FunctionNode) -> bool:
        """Whether the site is everything ``function`` does.

        A site that assigns the helper's returned variables is the whole body
        when the function ends by returning exactly those names: the
        function's result is then the tuple the call unpacks.
        """
        body = body_without_docstring(function.body)
        assigned = self._assigned_names(replacement.node)
        if assigned is None:
            return self._block_line_span(body) == tuple(replacement.line_range)
        if len(body) < 2 or self._return_positions(assigned, body[-1]) is None:
            return False
        return self._block_line_span(body[:-1]) == tuple(replacement.line_range)

    def _helper_reduced_to_forwarder(
        self, proposal: RefactoringProposal, functions: FunctionIndex
    ) -> Optional[str]:
        """The generated helper a site of ``proposal`` would reduce to a forwarder, if any.

        A helper this tool inserted on an earlier pass whose whole body is a
        site would keep only the new call: indirection with no logic of its
        own, and on a long input a chain of it. This remains true when every
        site is a whole body. Reuse, when proved, happens before this guard;
        merging two already-generated helpers would need to rewrite their
        existing consumers as well, and is not attempted here.
        """
        sites = [
            (
                replacement,
                functions.innermost_at(
                    replacement.file_path or proposal.file_path, replacement.line_range
                ),
            )
            for replacement in proposal.replacements
        ]
        inserted = [
            (replacement, site)
            for replacement, site in sites
            if site is not None
            and is_generated_helper_name(site.node.name)
            and self._helper_introduced_during_run(
                site.node.name, replacement.file_path or proposal.file_path
            )
        ]
        if not inserted:
            # Names already in the input, even helper-shaped ones, keep their
            # own independent binding behavior by sharing a new helper. The
            # same applies when only one of the sites is a whole body.
            return None
        for replacement, site in inserted:
            if self._site_is_whole_body(replacement, site.node):
                return site.node.name
        if len(inserted) == len(sites):
            helper_bodies = {
                (
                    tuple(arg.arg for arg in site.node.args.posonlyargs + site.node.args.args),
                    tuple(ast.dump(statement) for statement in site.node.body),
                )
                for _, site in inserted
            }
            if len(helper_bodies) == 1:
                # Splitting equivalent helpers already introduced by separate
                # batches only adds another layer; their existing callers remain.
                return inserted[0][1].node.name
        return None

    def _helper_introduced_during_run(self, name: str, path: str) -> bool:
        """The private stage introduced this name; the original program did not spell it."""
        if self._output_origin is None:
            return False
        origin = self._origin_of(path)
        return Path(origin).resolve() != Path(path).resolve() and name not in words_of(
            read_source(origin)
        )

    def _reusing_generated_helper(
        self, proposal: RefactoringProposal, functions: FunctionIndex
    ) -> Optional[RefactoringProposal]:
        """Call a helper this run introduced when the proposal proves it is equivalent.

        Its whole body would become ``return new(old_parameters...)`` (or
        a bare call for a body returning nothing), with exactly its existing
        parameters in order. The other proposed calls can therefore call the
        existing helper, leaving its definition and signature untouched. The
        already-checked home is unchanged, so import-cycle and import-effect
        reasoning still applies; materialization still checks the project.

        Only a module helper absent from the original staged source is
        eligible. A helper-shaped name in user input remains an existing
        function whose monkeypatch behavior is part of the contract too.
        """
        if (
            self._output_origin is None
            or proposal.insert_into_class is not None
            or proposal.insert_into_function is not None
            or proposal.reused_function is not None
        ):
            return None
        sites = [
            (
                replacement,
                functions.innermost_at(
                    replacement.file_path or proposal.file_path, replacement.line_range
                ),
            )
            for replacement in proposal.replacements
        ]
        restated = [
            (replacement, site)
            for replacement, site in sites
            if site is not None
            and is_generated_helper_name(site.node.name)
            and self._site_is_whole_body(replacement, site.node)
        ]
        if len(restated) != 1:
            return None
        replacement, site = restated[0]
        helper = site.node
        path = replacement.file_path or proposal.file_path
        if (
            not isinstance(helper, ast.FunctionDef)
            or site.class_name is not None
            or site.enclosing_function is not None
            or helper.decorator_list
            or helper.args.defaults
            or Path(path).resolve() != Path(proposal.file_path).resolve()
        ):
            return None
        if not self._helper_introduced_during_run(helper.name, path):
            return None
        bindings = global_bindings(site.source)
        if (
            bindings is None
            or bindings.star_imports
            or helper.name in bindings.rebound_by_global
            or len(bindings.bindings.get(helper.name, ())) != 1
        ):
            return None
        parameters = self._positional_parameter_names(helper)
        node = replacement.node
        call = self._unwrap_helper_call(node, proposal.extracted_function.name)
        if (
            parameters is None
            or not isinstance(node, (ast.Return, ast.Expr))
            or call is None
            or [argument.id if isinstance(argument, ast.Name) else None for argument in call.args]
            != parameters
        ):
            return None
        others = []
        for other, other_site in sites:
            if other is replacement:
                continue
            if other_site is None or (
                dataclasses.replace(other.comments, argument_lines=frozenset()) != SiteComments()
            ):
                return None
            if other.class_name is not None and helper.name.startswith("__"):
                # A fresh name would be respelled for class mangling; this one
                # belongs to the existing module helper and cannot be changed.
                return None
            other_path = other.file_path or proposal.file_path
            if Path(other_path).resolve() != Path(path).resolve():
                # The normal fresh-name allocator avoids every existing word
                # in a borrower. Reuse must retain that same hygiene guarantee.
                if helper.name in words_of(other_site.source):
                    return None
            else:
                scope = other_site.scope_analyzer.node_scopes.get(other_site.node)
                if scope is None:
                    return None
                binding = scope.lookup(helper.name)
                if binding is None or binding.node is not helper:
                    return None
            copied = copy.deepcopy(other.node)
            other_call = self._unwrap_helper_call(copied, proposal.extracted_function.name)
            if other_call is None:
                return None
            other_call.func = ast.copy_location(
                ast.Name(id=helper.name, ctx=ast.Load()), other_call.func
            )
            others.append(dataclasses.replace(other, node=copied))
        if not others:
            return None
        return dataclasses.replace(
            proposal,
            extracted_function=copy.deepcopy(helper),
            replacements=others,
            description=f"Call {helper.name} ({Path(path).name}) in place of its restatement",
            parameters_count=len(parameters),
            return_variables=[],
            reused_function=ReusedFunction(
                helper.name, path, (helper.lineno, helper.end_lineno or helper.lineno)
            ),
            wants_type_inference=False,
            required_imports=(),
            type_checking_imports=(),
            helper_type_declarations=(),
            helper_comments=HelperComments(),
        )

    def _verify_reused_function_calls(
        self,
        modified_files: Dict[str, str],
        target: ReusedFunction,
        proposal: RefactoringProposal,
    ) -> None:
        """Fail loudly if the reused function is gone or a generated call cannot bind to it.

        Pre-existing calls are not checked: they may legitimately use keywords or
        rely on defaults. Only the calls this proposal generates must pass
        exactly the function's positional parameters.
        """
        source = modified_files.get(target.file_path)
        if source is None:
            source = read_source(target.file_path)
        definitions = [
            node
            for node in self._parse_source(source).body
            if isinstance(node, ast.FunctionDef) and node.name == target.name
        ]
        if not definitions:
            raise RefactoringError(
                f"Reused function {target.name} is no longer defined at module level: "
                f"{target.file_path}"
            )
        # ``@overload`` stubs precede the real definition; the last one is the
        # runtime binding the calls resolve to, as the redirect required.
        parameters = self._positional_parameter_names(definitions[-1])
        for replacement in proposal.replacements:
            call = self._unwrap_helper_call(replacement.node, target.name)
            if parameters is None or call is None or len(call.args) != len(parameters):
                raise RefactoringError(
                    f"Generated call to {target.name} does not bind its "
                    f"{len(parameters or [])} parameters: {replacement.file_path}"
                )
