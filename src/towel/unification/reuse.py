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
from typing import Dict, List, Optional, Tuple, cast
from .exceptions import RefactoringError
from .models import FunctionArtifact, RefactoringProposal, Replacement, ReusedFunction
from .pipeline import parse_cached
from .scope_analyzer import ScopeBinding
from .semantic_safety import would_create_import_cycle
from .visitors import body_without_docstring

from .engine_state import EngineState
from .function_index import FunctionIndex


@dataclass(frozen=True)
class _ReusePlan:
    """How a helper's arguments map onto an existing function it restates.

    ``parameter_positions[j]`` is the helper argument index that supplies the
    function's j-th positional parameter. ``ambient`` maps the remaining
    argument indices to the module-level name (and its binding at the
    function's own site) that the function reads for itself.
    """

    parameter_positions: List[int]
    ambient: Dict[int, Tuple[str, Optional["ScopeBinding"]]]


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
        if not isinstance(statement, (ast.Return, ast.Expr)):
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
    def _same_absolute_import(left: ast.AST, right: ast.AST, name: str) -> bool:
        """Whether two import statements bind ``name`` to the same absolute target.

        ``import a.b`` in two modules binds the same module object; ``from a
        import b`` (absolute) binds the same attribute. A relative import means
        something different in each package, so it never counts.
        """
        if isinstance(left, ast.Import) and isinstance(right, ast.Import):
            left_targets = {
                alias.name
                for alias in left.names
                if (alias.asname or alias.name.split(".")[0]) == name
            }
            right_targets = {
                alias.name
                for alias in right.names
                if (alias.asname or alias.name.split(".")[0]) == name
            }
            return bool(left_targets) and left_targets == right_targets
        if isinstance(left, ast.ImportFrom) and isinstance(right, ast.ImportFrom):
            if left.level or right.level or left.module != right.module:
                return False
            left_targets = {
                alias.name for alias in left.names if (alias.asname or alias.name) == name
            }
            right_targets = {
                alias.name for alias in right.names if (alias.asname or alias.name) == name
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
        names = [arg.id if isinstance(arg, ast.Name) else None for arg in call.args]
        if None in names or len(set(names)) != len(names):
            return None
        if not set(parameters) <= set(names):
            return None
        scope = target.scope_analyzer.node_scopes.get(target.node)
        if scope is None:
            return None
        ambient: Dict[int, Tuple[str, Optional[ScopeBinding]]] = {}
        for index, name in enumerate(names):
            if name in parameters:
                continue
            binding = scope.lookup(cast(str, name))
            if binding is not None and binding.scope_id != target.root_scope.scope_id:
                return None
            ambient[index] = (cast(str, name), binding)
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
        """The plain module-level function whose whole body ``replacement`` covers, if any.

        Only a function that a call by name reproduces qualifies: defined once,
        unconditionally, at module level, without decorators, not async (the
        generated helper is synchronous), and never rebound or deleted through a
        ``global`` declaration or a module-level ``del``.
        """
        file_path = replacement.file_path or default_file
        for artifact in functions.in_file(file_path):
            function = artifact.node
            if (
                artifact.class_name is not None
                or artifact.enclosing_function is not None
                or not isinstance(function, ast.FunctionDef)
                or function.decorator_list
                or self._block_line_span(body_without_docstring(function.body))
                != tuple(replacement.line_range)
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
        """
        if proposal.return_variables:
            return None
        helper_name = proposal.extracted_function.name
        candidates: List[Tuple[int, FunctionArtifact]] = []
        for index, replacement in enumerate(proposal.replacements):
            target = self._reusable_function_at(replacement, proposal.file_path, functions)
            if target is not None:
                candidates.append((index, target))
        candidates.sort(key=lambda item: (item[1].file_path, item[1].node.lineno))
        for index, target in candidates:
            call = self._unwrap_helper_call(proposal.replacements[index].node, helper_name)
            if call is None:
                continue
            plan = self._reuse_plan(call, target)
            if plan is None:
                continue
            others = [
                replacement
                for position, replacement in enumerate(proposal.replacements)
                if position != index
            ]
            sites = [
                functions.innermost_at(
                    replacement.file_path or proposal.file_path, replacement.line_range
                )
                for replacement in others
            ]
            if any(site is None for site in sites):
                continue
            rewritten = [
                self._call_to_existing_function(
                    replacement, helper_name, target, plan, cast(FunctionArtifact, site)
                )
                for replacement, site in zip(others, sites)
            ]
            if any(replacement is None for replacement in rewritten):
                continue
            if not all(
                self._existing_function_reachable(
                    target, replacement, proposal.file_path, functions
                )
                for replacement in others
            ):
                continue
            participating = {target.file_path} | {
                replacement.file_path or proposal.file_path for replacement in others
            }
            if would_create_import_cycle(target.file_path, participating, self.import_graph):
                continue
            callers = sorted({cast(FunctionArtifact, site).node.name for site in sites})
            location = Path(target.file_path).name
            return dataclasses.replace(
                proposal,
                file_path=target.file_path,
                extracted_function=cast(ast.FunctionDef, copy.deepcopy(target.node)),
                replacements=cast(List[Replacement], rewritten),
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
            source = Path(target.file_path).read_text(encoding="utf-8")
        definitions = [
            node
            for node in parse_cached(source).body
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
