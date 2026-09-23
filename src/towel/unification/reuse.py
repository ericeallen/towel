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
What remains here is the quality rule that a helper an earlier pass inserted
is not reduced to a forwarder, and the check of generated calls to a reused
function that a proposal built elsewhere may still name.
"""

from __future__ import annotations

import ast

from typing import Dict, List, Optional, Sequence, Tuple
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
        own, and on a long input a chain of it. When every site is the whole
        body of its function, though, each of them becomes a call of the new
        helper, the earlier helper among them: no function is redirected to
        another, which would let rebinding one change the other, and that is
        the one way to share the code.
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
        if all(
            site is not None and self._site_is_whole_body(replacement, site.node)
            for replacement, site in sites
        ):
            return None
        for replacement, site in sites:
            if site is None or not is_generated_helper_name(site.node.name):
                continue
            if self._site_is_whole_body(replacement, site.node):
                return site.node.name
        return None

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
