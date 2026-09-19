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

"""Calling an existing function instead of extracting its body again.

When a duplicate is the whole body of a plain module-level function, the
helper would restate that function. The function is kept and the other
sites call it, with arguments in its parameter order and names its module
binds left ambient. The redirect rests on the helper already verified by
instantiation, checks that every generated call binds the function's
positional parameters against its last definition, and refuses a call
across files that would close an import cycle.
"""

from __future__ import annotations

import ast
import copy
import dataclasses

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from .exceptions import RefactoringError
from .models import (
    FunctionArtifact,
    FunctionNode,
    RefactoringProposal,
    Replacement,
    ReusedFunction,
    is_generated_helper_name,
)
from .scope_analyzer import ScopeBinding
from .semantic_safety import module_resolved_names
from .statement_facts import imported_binding_name
from .import_graph import import_runs_new_code, would_create_import_cycle
from .visitors import body_without_docstring

from .engine_state import EngineState
from ..source_text import read_source
from .function_index import FunctionIndex


@dataclass(frozen=True)
class _ReusePlan:
    """How a helper's arguments map onto an existing function it restates.

    ``parameter_positions[j]`` is the helper argument index that supplies the
    function's j-th positional parameter. ``ambient`` maps the remaining
    argument indices to the module-level name (and its binding at the
    function's own site) that the function reads for itself.
    ``return_positions`` reorders each site's assignment targets into the
    order the function returns its values.
    """

    parameter_positions: List[int]
    ambient: Dict[int, Tuple[str, Optional["ScopeBinding"]]]
    # For each name the function's final ``return`` yields, its index in the
    # generated assignment; empty when the sites do not assign a result.
    return_positions: Tuple[int, ...] = ()


def _store_target(names: Sequence[str]) -> ast.expr:
    """The assignment target binding ``names``: one name, or a tuple of them."""
    if len(names) == 1:
        return ast.Name(id=names[0], ctx=ast.Store())
    return ast.Tuple(elts=[ast.Name(id=name, ctx=ast.Store()) for name in names], ctx=ast.Store())


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
        own, and on a long input a chain of it. Redirecting the proposal to
        that helper (``_redirect_to_existing_function``) comes first; this is
        for when its parameters differ from the new helper's.
        """
        for replacement in proposal.replacements:
            file_path = replacement.file_path or proposal.file_path
            site = functions.innermost_at(file_path, replacement.line_range)
            if site is None or not is_generated_helper_name(site.node.name):
                continue
            if self._site_is_whole_body(replacement, site.node):
                return site.node.name
        return None

    @staticmethod
    def _same_absolute_import(left: ast.AST, right: ast.AST, name: str) -> bool:
        """Whether two import statements bind ``name`` to the same absolute target.

        ``import a.b`` in two modules binds the same module object; ``from a
        import b`` (absolute) binds the same attribute. A relative import means
        something different in each package, so it never counts.
        """
        if isinstance(left, ast.Import) and isinstance(right, ast.Import):
            left_targets = {
                alias.name for alias in left.names if imported_binding_name(alias) == name
            }
            right_targets = {
                alias.name for alias in right.names if imported_binding_name(alias) == name
            }
            return bool(left_targets) and left_targets == right_targets
        if isinstance(left, ast.ImportFrom) and isinstance(right, ast.ImportFrom):
            if left.level or right.level or left.module != right.module:
                return False
            left_targets = {
                alias.name for alias in left.names if imported_binding_name(alias) == name
            }
            right_targets = {
                alias.name for alias in right.names if imported_binding_name(alias) == name
            }
            return bool(left_targets) and left_targets == right_targets
        return False

    def _reuse_plan(self, call: ast.Call, target: FunctionArtifact) -> Optional["_ReusePlan"]:
        """How the helper's arguments map onto the target function, or None.

        The call at the function's own site must pass each of its positional
        parameters exactly once, by name. Any other argument must be a name
        that resolves there to a module-level binding of the target's module
        (a function, class, or import the body reads): the function reads it
        itself, so a caller need not pass it. Then the helper applied to any
        arguments is the function applied to the parameter arguments, provided
        every other site supplies the same module-level objects.
        """
        if not isinstance(target.node, ast.FunctionDef):
            return None
        parameters = self._positional_parameter_names(target.node)
        if parameters is None:
            return None
        if not all(isinstance(arg, ast.Name) for arg in call.args):
            return None
        names = [arg.id for arg in call.args if isinstance(arg, ast.Name)]
        if len(set(names)) != len(names):
            return None
        if not set(parameters) <= set(names):
            return None
        ambient_names = set(names) - set(parameters)
        if (
            module_resolved_names(target.node, target.scope_analyzer, ambient_names)
            != ambient_names
        ):
            # Same-spelled type parameters of different generic functions are
            # different lexical bindings, never ambient module objects.
            return None
        scope = target.scope_analyzer.node_scopes.get(target.node)
        if scope is None:
            return None
        ambient: Dict[int, Tuple[str, Optional[ScopeBinding]]] = {}
        for index, name in enumerate(names):
            if name in parameters:
                continue
            binding = scope.lookup(name)
            if binding is not None and binding.scope_id != target.root_scope.scope_id:
                return None
            ambient[index] = (name, binding)
        return _ReusePlan(
            parameter_positions=[names.index(parameter) for parameter in parameters],
            ambient=ambient,
        )

    @staticmethod
    def _module_deletes_name(tree: ast.Module, name: str) -> bool:
        """Whether a module-level statement (outside any definition) deletes ``name``."""
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(statement):
                if isinstance(node, ast.Delete) and any(
                    isinstance(target, ast.Name) and target.id == name for target in node.targets
                ):
                    return True
        return False

    def _reusable_function_at(
        self,
        replacement: Replacement,
        default_file: str,
        functions: FunctionIndex,
    ) -> Optional[FunctionArtifact]:
        """The plain module-level function whose whole body ``replacement`` is, if any.

        Only a function that a call by name reproduces qualifies: defined once,
        unconditionally, at module level, without decorators, not async (the
        generated helper is synchronous), and never rebound or deleted through a
        ``global`` declaration or a module-level ``del``.
        """
        file_path = replacement.file_path or default_file
        for artifact in functions.body_starting_at(file_path, replacement.line_range[0]):
            function = artifact.node
            if (
                artifact.class_name is not None
                or artifact.enclosing_function is not None
                or not isinstance(function, ast.FunctionDef)
                or function.decorator_list
                or not self._site_is_whole_body(replacement, function)
            ):
                continue
            tree = artifact.scope_analyzer.analyzed_tree
            binding = artifact.root_scope.bindings.get(function.name)
            if (
                not isinstance(tree, ast.Module)
                or not any(statement is function for statement in tree.body)
                or binding is None
                or binding.node is not function
                or any(
                    function.name in names for names in artifact.scope_analyzer.global_vars.values()
                )
                or self._module_deletes_name(tree, function.name)
            ):
                return None
            return artifact
        return None

    def _existing_function_reachable(
        self,
        target: FunctionArtifact,
        replacement: Replacement,
        default_file: str,
        functions: FunctionIndex,
    ) -> bool:
        """Whether a call by name at the replacement site resolves to ``target``.

        In the target's own module the name must resolve through the site's
        enclosing scopes to that definition; in another module it must be free
        there, so the import the materializer adds is what binds it. A
        class-private spelling is mangled inside a class body either way.
        """
        file_path = replacement.file_path or default_file
        name = target.node.name
        if replacement.class_name is not None and name.startswith("__") and not name.endswith("__"):
            return False
        site = functions.innermost_at(file_path, replacement.line_range)
        if site is None:
            return False
        scope = site.scope_analyzer.node_scopes.get(site.node)
        if scope is None:
            return False
        binding = scope.lookup(name)
        if file_path == target.file_path:
            return binding is not None and binding.node is target.node
        return binding is None

    def _call_to_existing_function(
        self,
        replacement: Replacement,
        helper_name: str,
        target: FunctionArtifact,
        plan: "_ReusePlan",
        site: FunctionArtifact,
    ) -> Optional[Replacement]:
        """The replacement with its helper call retargeted at the existing function.

        Parameter arguments are reordered into the function's order; when that
        order differs from the helper's, only names and constants may move,
        since evaluating other expressions in a new order could be observed.
        An ambient argument is dropped, but only when it is the same name bound
        to the same module-level object as at the function's own site: the same
        definition in the same module, or an identical absolute import.
        """
        node = copy.deepcopy(replacement.node)
        call = self._unwrap_helper_call(node, helper_name)
        if call is None or len(call.args) != len(plan.parameter_positions) + len(plan.ambient):
            return None
        site_scope = site.scope_analyzer.node_scopes.get(site.node)
        if site_scope is None:
            return None
        for index, (name, target_binding) in plan.ambient.items():
            argument = call.args[index]
            if not isinstance(argument, ast.Name) or argument.id != name:
                return None
            site_binding = site_scope.lookup(name)
            if site.file_path == target.file_path:
                same = (
                    site_binding is target_binding
                    if target_binding is None
                    else site_binding is not None and site_binding.node is target_binding.node
                )
            else:
                same = (
                    site_binding is not None
                    and target_binding is not None
                    and site_binding.scope_id == site.root_scope.scope_id
                    and self._same_absolute_import(site_binding.node, target_binding.node, name)
                )
            if not same:
                return None
        parameter_arguments = [call.args[index] for index in plan.parameter_positions]
        identity = plan.parameter_positions == sorted(plan.parameter_positions)
        if not identity and not all(
            isinstance(arg, (ast.Name, ast.Constant)) for arg in parameter_arguments
        ):
            return None
        call.args = parameter_arguments
        call.func = ast.Name(id=target.node.name, ctx=ast.Load())
        assigned = self._assigned_names(node)
        if isinstance(node, ast.Assign) and assigned is not None and plan.return_positions:
            reordered = [assigned[position] for position in plan.return_positions]
            node.targets = [_store_target(reordered)]
        return dataclasses.replace(replacement, node=node)

    def _redirect_to_existing_function(
        self,
        proposal: RefactoringProposal,
        functions: FunctionIndex,
    ) -> Optional[RefactoringProposal]:
        """The proposal rewritten to call a function that one duplicate already is.

        When a duplicate site is the whole body of a plain module-level function
        and the generated call there passes exactly that function's parameters,
        the helper applied to any arguments is that function applied to them.
        The fresh helper would only restate the function, so it is dropped: the
        function stays as it is and every other site calls it. Candidates are
        tried in source order; one the other sites cannot reach by name, or
        whose import would close a cycle, is skipped. None keeps the extraction.

        A site that assigns what the helper returns qualifies when the
        function ends by returning exactly those names, in any order; the
        other sites then unpack the function's result in its order.
        """
        helper_name = proposal.extracted_function.name
        candidates: List[Tuple[int, FunctionArtifact]] = []
        for index, replacement in enumerate(proposal.replacements):
            target = self._reusable_function_at(replacement, proposal.file_path, functions)
            if target is not None:
                candidates.append((index, target))
        candidates.sort(key=lambda item: (item[1].file_path, item[1].node.lineno))
        for index, target in candidates:
            redirected = self._redirected_through(proposal, index, target, helper_name, functions)
            if redirected is not None:
                return redirected
        return None

    def _redirected_through(
        self,
        proposal: RefactoringProposal,
        index: int,
        target: FunctionArtifact,
        helper_name: str,
        functions: FunctionIndex,
    ) -> Optional[RefactoringProposal]:
        """The proposal redirected through ``target``, or None when one site cannot follow.

        The site at ``index`` is the whole body of ``target``. Every other site
        must be a plain call the function's parameters can take, must reach the
        function by name, and its module must not gain an import that closes a
        cycle or runs code it did not run before.
        """
        function = target.node
        call = self._unwrap_helper_call(proposal.replacements[index].node, helper_name)
        if call is None or not isinstance(function, ast.FunctionDef):
            return None
        plan = self._reuse_plan(call, target)
        if plan is None:
            return None
        assigned = self._assigned_names(proposal.replacements[index].node)
        if assigned is not None:
            final = body_without_docstring(target.node.body)[-1]
            plan = dataclasses.replace(
                plan, return_positions=self._return_positions(assigned, final) or ()
            )
        others = [
            replacement
            for position, replacement in enumerate(proposal.replacements)
            if position != index
        ]
        sites = [
            site
            for replacement in others
            if (
                site := functions.innermost_at(
                    replacement.file_path or proposal.file_path, replacement.line_range
                )
            )
            is not None
        ]
        if len(sites) != len(others):
            return None
        rewritten = [
            rewritten_site
            for replacement, site in zip(others, sites)
            if (
                rewritten_site := self._call_to_existing_function(
                    replacement, helper_name, target, plan, site
                )
            )
            is not None
        ]
        if len(rewritten) != len(others):
            return None
        if not all(
            self._existing_function_reachable(target, replacement, proposal.file_path, functions)
            for replacement in others
        ):
            return None
        borrowers = {replacement.file_path or proposal.file_path for replacement in others}
        participating = {target.file_path} | borrowers
        if would_create_import_cycle(target.file_path, participating, self.import_graph):
            return None
        if any(
            import_runs_new_code(target.file_path, borrower, self.import_graph)
            for borrower in borrowers - {target.file_path}
        ):
            return None
        callers = sorted({site.node.name for site in sites})
        location = Path(target.file_path).name
        return dataclasses.replace(
            proposal,
            file_path=target.file_path,
            extracted_function=copy.deepcopy(function),
            replacements=rewritten,
            description=(
                f"Reuse {target.node.name} ({location}) for duplicated code in "
                + ", ".join(callers)
            ),
            insert_into_class=None,
            insert_into_function=None,
            method_kind=None,
            method_param_name=None,
            reused_function=ReusedFunction(
                name=target.node.name,
                file_path=target.file_path,
                line_range=(target.node.lineno, target.node.end_lineno or target.node.lineno),
            ),
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
