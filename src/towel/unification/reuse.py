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

"""Reuse proved whole-body functions when their program never rebinds them.

Relaxed mode requires a clean program scan. Strict mode additionally requires
complete consumer scope and callers with no ownership transfer. Introduced
helpers retain the separately proved reuse and alias path below.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import logging
from dataclasses import dataclass
from pathlib import Path

from typing import Dict, List, Optional, Sequence, Tuple
from .annotation_imports import words_of
from .block_comments import HelperComments, SiteComments
from .exceptions import RefactoringError
from .models import (
    FunctionNode,
    FunctionArtifact,
    RefactoringProposal,
    Replacement,
    ReusedFunction,
    is_generated_helper_name,
)
from .visitors import annotation_expressions, body_without_docstring

from .engine_state import EngineState
from ..canonical_ast import canonical_dump
from ..source_text import read_source
from .function_index import FunctionIndex
from .import_graph import imported_alias_sites
from .module_bindings import global_bindings
from .import_graph import import_runs_new_code, would_create_import_cycle
from .rebinding import rebinding_reason


@dataclass(frozen=True)
class _ReusePlan:
    """How a helper's arguments map onto an existing function it restates.

    ``parameter_positions[j]`` is the helper argument index that supplies the
    function's j-th positional parameter. Every argument must be one of
    those parameters: dropping ambient arguments could change their binding.
    ``return_positions`` reorders each site's assignment targets into the
    order the function returns its values.
    """

    parameter_positions: Tuple[int, ...]
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
        # Retained local lifetimes can make a full-body call an assignment,
        # even when the original function ends with an implicit None return.
        if self._block_line_span(body) == tuple(replacement.line_range):
            return True
        assigned = self._assigned_names(replacement.node)
        if assigned is None:
            return False
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
        equivalent generated module helpers may instead keep their existing
        consumers through an alias. Other forwarding layers are declined.
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
                    tuple(canonical_dump(statement) for statement in site.node.body),
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
        eligible. Another such helper with the exact same signature and body
        may become an alias, keeping its callers without a forwarding layer.
        A helper-shaped name in user input remains an existing function whose
        monkeypatch behavior is part of the contract too.
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
            and Path(replacement.file_path or proposal.file_path).resolve()
            == Path(proposal.file_path).resolve()
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
            if (
                is_generated_helper_name(other_site.node.name)
                and self._helper_introduced_during_run(other_site.node.name, other_path)
                and self._site_is_whole_body(other, other_site.node)
            ):
                alias = self._alias_generated_helper(other, other_site, helper, other_call)
                if alias is None:
                    return None
                others.append(alias)
                continue
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

    def _alias_generated_helper(
        self,
        replacement: Replacement,
        site: FunctionArtifact,
        target: ast.FunctionDef,
        call: ast.Call,
    ) -> Optional[Replacement]:
        """Keep a proved generated helper's callers through an identical module function.

        Removing a definition also removes its evaluated header. Permit only
        plain positional signatures with inert string/None annotations, and
        retain the body and argument-order proof in addition to structural
        equality. Existing input functions never reach this branch.
        """
        function = site.node
        if (
            not isinstance(function, ast.FunctionDef)
            or site.class_name is not None
            or site.enclosing_function is not None
            or function.col_offset != 0
            or function.decorator_list
            or function.args.defaults
            or getattr(function, "type_params", False)
            or function.type_comment
            or self._positional_parameter_names(function) is None
            or canonical_dump(function.args) != canonical_dump(target.args)
            or [canonical_dump(node) for node in function.body]
            != [canonical_dump(node) for node in target.body]
            or (canonical_dump(function.returns) if function.returns else None)
            != (canonical_dump(target.returns) if target.returns else None)
            or any(
                not isinstance(annotation, ast.Constant)
                or not (isinstance(annotation.value, str) or annotation.value is None)
                for annotation in annotation_expressions(function)
            )
            or [arg.id if isinstance(arg, ast.Name) else None for arg in call.args]
            != self._positional_parameter_names(function)
        ):
            return None
        bindings = global_bindings(site.source)
        if (
            bindings is None
            or bindings.star_imports
            or function.name in bindings.rebound_by_global
            or len(bindings.bindings.get(function.name, ())) != 1
        ):
            return None
        return dataclasses.replace(
            replacement,
            line_range=(function.lineno, function.end_lineno or function.lineno),
            columns=None,
            node=ast.fix_missing_locations(
                ast.Assign(
                    targets=[ast.Name(id=function.name, ctx=ast.Store())],
                    value=ast.Name(id=target.name, ctx=ast.Load()),
                )
            ),
        )

    def _reuse_plan(self, call: ast.Call, target: FunctionArtifact) -> Optional[_ReusePlan]:
        """Map the helper's arguments bijectively onto the function's parameters.

        Ambient arguments cannot be dropped: identical imports in two modules
        still have independent bindings, either of which the program can rebind.
        """
        if not isinstance(target.node, ast.FunctionDef):
            return None
        parameters = self._positional_parameter_names(target.node)
        names = [argument.id for argument in call.args if isinstance(argument, ast.Name)]
        if (
            parameters is None
            or len(names) != len(call.args)
            or len(set(names)) != len(names)
            or set(names) != set(parameters)
        ):
            return None
        return _ReusePlan(tuple(names.index(parameter) for parameter in parameters))

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
            if not isinstance(tree, ast.Module) or not any(
                statement is function for statement in tree.body
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
        if binding is None:
            return True
        statement = binding.node
        if binding.scope_id != site.root_scope.scope_id or not isinstance(
            statement, ast.ImportFrom
        ):
            return False
        return any(
            (alias.asname or alias.name) == name
            and imported_alias_sites(file_path, statement, alias, "", self.import_graph)
            == frozenset({(Path(target.file_path).resolve(), name)})
            for alias in statement.names
        )

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
        """
        node = copy.deepcopy(replacement.node)
        call = self._unwrap_helper_call(node, helper_name)
        if call is None or len(call.args) != len(plan.parameter_positions):
            return None
        parameter_arguments = [call.args[index] for index in plan.parameter_positions]
        identity = plan.parameter_positions == tuple(sorted(plan.parameter_positions))
        if not identity and not all(
            isinstance(arg, (ast.Name, ast.Constant)) for arg in parameter_arguments
        ):
            return None
        call.args = parameter_arguments
        call.func = ast.Name(id=target.node.name, ctx=ast.Load())
        assigned = self._assigned_names(node)
        if isinstance(node, ast.Assign) and assigned is not None and plan.return_positions:
            if self.strict and plan.return_positions != tuple(sorted(plan.return_positions)):
                # First stores allocate frame slots; permuting them changes
                # the order in which their values are released on frame exit.
                return None
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
        if self.strict and not self.complete_scope:
            return None
        helper_name = proposal.extracted_function.name
        candidates: List[Tuple[int, FunctionArtifact]] = []
        for index, replacement in enumerate(proposal.replacements):
            target = self._reusable_function_at(replacement, proposal.file_path, functions)
            if target is not None and not (
                is_generated_helper_name(target.node.name)
                and self._helper_introduced_during_run(target.node.name, target.file_path)
            ):
                candidates.append((index, target))
        candidates.sort(key=lambda item: (item[1].file_path, item[1].node.lineno))
        for index, target in candidates:
            if self.strict and any(
                replacement.argument_handoff is not None
                for position, replacement in enumerate(proposal.replacements)
                if position != index
            ):
                continue
            reason = rebinding_reason(self._rebinding_sources, target.file_path, target.node)
            if reason is not None:
                if reason not in self._reported_rebindings:
                    self._reported_rebindings.add(reason)
                    logging.getLogger("towel").warning(reason)
                continue
            redirected = self._redirected_through(proposal, index, target, helper_name, functions)
            if redirected is not None:
                if any(
                    (replacement.file_path or redirected.file_path) != redirected.file_path
                    for replacement in redirected.replacements
                ):
                    try:
                        self._require_early_reused_helper(redirected)
                    except RefactoringError:
                        continue
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
            positions = self._return_positions(assigned, final)
            if positions is None:
                return None
            plan = dataclasses.replace(plan, return_positions=positions)
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
        if len(sites) != len(others) or any(
            dataclasses.replace(replacement.comments, argument_lines=frozenset()) != SiteComments()
            for replacement in others
        ):
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
            wants_type_inference=False,
            required_imports=(),
            type_checking_imports=(),
            helper_type_declarations=(),
            helper_comments=HelperComments(),
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
        """Fail if the reused function is gone or a generated call/alias cannot bind to it.

        Pre-existing calls are not checked: they may legitimately use keywords or
        rely on defaults. Only the calls this proposal generates must pass
        exactly the function's positional parameters. An alias must import
        precisely the proved function before evaluating the assignment.
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
            node = replacement.node
            if (
                parameters is not None
                and isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and is_generated_helper_name(node.targets[0].id)
                and isinstance(node.value, ast.Name)
                and node.value.id == target.name
                and self._helper_introduced_during_run(
                    node.targets[0].id, replacement.file_path or proposal.file_path
                )
            ):
                self._verify_reused_alias(modified_files, replacement, target, node.targets[0].id)
                continue
            call = self._unwrap_helper_call(replacement.node, target.name)
            if parameters is None or call is None or len(call.args) != len(parameters):
                raise RefactoringError(
                    f"Generated call to {target.name} does not bind its "
                    f"{len(parameters or [])} parameters: {replacement.file_path}"
                )

    def _verify_reused_alias(
        self,
        modified_files: Dict[str, str],
        replacement: Replacement,
        target: ReusedFunction,
        alias_name: str,
    ) -> None:
        """The rendered alias must read an earlier import of precisely the proved target.

        Placement already checked this edge's cycles and import effects. Check
        its rendered binding too: syntax alone does not establish that the
        target is available when a module-level alias executes.
        """
        path = replacement.file_path or target.file_path
        source = modified_files[path]
        tree = self._parse_source(source)
        bindings = global_bindings(source)
        aliases = bindings.bindings.get(alias_name, ()) if bindings is not None else ()
        if (
            bindings is not None
            and not bindings.star_imports
            and alias_name not in bindings.rebound_by_global
            and len(aliases) == 1
            and aliases[0].certain
        ):
            assignment = tree.body[aliases[0].order]
            binding = bindings.in_effect(target.name, aliases[0].order)
            if (
                isinstance(assignment, ast.Assign)
                and isinstance(assignment.value, ast.Name)
                and assignment.value.id == target.name
                and binding is not None
                and binding.is_import
                and isinstance(statement := tree.body[binding.order], ast.ImportFrom)
            ):
                for imported in statement.names:
                    if (imported.asname or imported.name) == target.name:
                        sites = imported_alias_sites(
                            path, statement, imported, "", self.import_graph
                        )
                        if sites == frozenset({(Path(target.file_path).resolve(), target.name)}):
                            return
        raise RefactoringError(
            f"Generated alias {alias_name} does not read the proved helper "
            f"{target.name} from {target.file_path}: {path}"
        )
