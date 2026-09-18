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

"""Facts about one block within its function, and the filters that read them.

What a block binds, reads, and returns; whether it produces a value;
whether it reassigns a name it did not bind; which names must be declared
global or nonlocal in a helper; and the two filters that decline helpers
which only forward or rename. Every answer is memoized per block structure
or per function, since the pair stages ask the same questions of the same
blocks many times.
"""

from __future__ import annotations

import ast

from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple, TypeVar, cast
from .assignment_analyzer import (
    _collect_bindings_and_reassignments,
    _collect_block_binding_stats,
    analyze_assignments,
)
from .block_signature import BlockSignature, extract_block_signature
from .extractor import has_complete_return_coverage, is_value_producing
from .models import (
    BlockBindingSnapshot,
    CodeBlockPair,
    FunctionNode,
    RejectReason,
    encloses,
)
from .parameters import parameter_names
from .scope_analyzer import ScopeAnalyzer
from .structural_memo import load_substitution, store_substitution
from .substitution import Substitution
from .visitors import (
    AssignTargetVisitor,
    AugAssignFinder,
    NameCollector,
    body_without_docstring,
)
from ..diagnostics import VALIDATION, debugging

from .engine_state import EngineState, GuardKey
from .function_index import FunctionIndex

T = TypeVar("T")


def align_return_variables(
    first: Set[str],
    second: Set[str],
    bound_first: Set[str],
    bound_second: Set[str],
    renames: Sequence[Dict[str, str]],
) -> Optional[Tuple[List[str], List[str]]]:
    """Order both blocks' live variables so one helper return serves every call.

    Each block reads its own set of names after the block, possibly under
    different spellings unified by alpha-renaming. The helper returns the
    union, spelled in the template's names and sorted; each call assigns the
    same positions under its own spelling. ``None`` means a live variable of
    one block has no binding in the other, so no single helper can return it.
    """
    template_renames = renames[0] if renames else {}
    block_renames = renames[1] if len(renames) > 1 else {}
    canonical_to_template = {canonical: name for name, canonical in template_renames.items()}
    canonical_to_block = {canonical: name for name, canonical in block_renames.items()}

    def to_template(name: str) -> str:
        canonical = block_renames.get(name, name)
        return canonical_to_template.get(canonical, canonical)

    def to_block(name: str) -> str:
        canonical = template_renames.get(name, name)
        return canonical_to_block.get(canonical, canonical)

    template_names = sorted(set(first) | {to_template(name) for name in second})
    block_names = [to_block(name) for name in template_names]
    if not set(template_names) <= bound_first or not set(block_names) <= bound_second:
        return None
    return template_names, block_names


class BlockAnalysis(EngineState):
    """See the module docstring."""

    def _unify_memoized(
        self,
        blocks: Sequence[Sequence[ast.AST]],
        hygienic_renames: List[Dict[str, str]],
        paths: Sequence[Optional[str]] = (),
    ) -> Optional[Substitution]:
        """Unify two blocks, reusing the result for any pair with the same structure."""
        if len(blocks) != 2:
            return self.unifier.unify_blocks(blocks, hygienic_renames)
        key = (self._sid(blocks[0]), self._sid(blocks[1]))
        if key in self._unify_cache:
            stored = self._unify_cache[key]
            if stored is None:
                return None
            substitution, renames = load_substitution(stored, blocks)
            for target, source in zip(hygienic_renames, renames):
                target.clear()
                target.update(source)
            return substitution
        result = self.unifier.unify_blocks(blocks, hygienic_renames)
        self._unify_cache[key] = (
            None if result is None else store_substitution(result, blocks, hygienic_renames)
        )
        return result

    def _block_rejected(
        self,
        guard: Callable[..., bool],
        nodes: Sequence[ast.AST],
        func: Optional[FunctionNode] = None,
        analyzer: Optional[ScopeAnalyzer] = None,
        path: Optional[str] = None,
        *,
        function_id: Optional[str] = None,
        block_id: Optional[str] = None,
    ) -> bool:
        """Evaluate a pure block guard once per (guard, function, block).

        A caller that evaluates several guards of one pair passes the
        structural ids it computed once; otherwise they are computed here.
        """
        if function_id is None and func is not None:
            function_id = self._sid([func])
        if block_id is None:
            block_id = self._sid(nodes)
        key = GuardKey(
            guard,
            function_id,
            block_id,
            self._module_digest(func) if analyzer is not None else None,
        )
        cached = self._block_guard_cache.get(key)
        if cached is not None:
            return cached
        if analyzer is not None:
            verdict = bool(guard(analyzer, func, list(nodes)))
        elif func is not None:
            verdict = bool(guard(func, list(nodes)))
        else:
            verdict = bool(guard(list(nodes)))
        self._block_guard_cache[key] = verdict
        return verdict

    def _is_value_producing(self, block: Sequence[ast.stmt]) -> bool:
        """``is_value_producing`` memoized per block: a block is paired many times."""
        if not block:
            return False
        first = block[0]
        by_length = self._value_producing_cache.get(first)
        if by_length is None:
            by_length = {}
            self._value_producing_cache[first] = by_length
        result = by_length.get(len(block))
        if result is None:
            result = is_value_producing(block)
            by_length[len(block)] = result
        return result

    def _get_assignment_reuse(self, func: FunctionNode) -> Dict[int, bool]:
        """Return (and cache) assignment analysis for a function definition."""
        cached = self._assignment_cache.get(func)
        if cached is not None:
            return cached
        analysis = analyze_assignments(func)
        self._assignment_cache[func] = analysis
        return analysis

    def _signed_blocks(
        self, function: FunctionNode
    ) -> List[Tuple[Tuple[int, int], List[ast.stmt], BlockSignature]]:
        """Share one block/signature enumeration across pairing and clustering."""
        cached = self._signed_block_cache.get(function)
        if cached is None:
            cached = [
                (span, nodes, extract_block_signature(nodes))
                for span, nodes in self._extract_code_blocks(function)
                if span[1] - span[0] + 1 >= self.min_lines
            ]
            self._signed_block_cache[function] = cached
            self._remember(
                (self._function_paths.get(function),), self._signed_block_cache, function
            )
        return cached

    def _extract_code_blocks(
        self, function: FunctionNode
    ) -> List[Tuple[Tuple[int, int], List[ast.stmt]]]:
        """
        Extract all contiguous code blocks from a function body, including nested bodies.

        Args:
            function: Function definition

        Returns:
            List of (line_range, statements) tuples
        """

        def extract_from_body(
            body: List[ast.stmt], parent: Optional[ast.stmt] = None
        ) -> List[Tuple[Tuple[int, int], List[ast.stmt]]]:
            # Extract all contiguous subsequences of minimum length from a given body
            results: List[Tuple[Tuple[int, int], List[ast.stmt]]] = []

            # An ``elif`` is the sole statement of its parent's ``orelse`` and
            # shares the parent's column. It has no position of its own in the
            # source, so a block starting there would be rendered as a sibling
            # of the parent ``if`` and run unconditionally. Its own body and
            # branches are still visited below.
            is_elif = (
                isinstance(parent, ast.If)
                and len(body) == 1
                and isinstance(body[0], ast.If)
                and body[0].col_offset == parent.col_offset
            )

            # Extract all contiguous subsequences
            for length in range(0 if is_elif else len(body), 0, -1):
                for start in range(len(body) - length + 1):
                    block = body[start : start + length]

                    # Do not extract blocks that contain nested function/class definitions.
                    # These statements establish new scopes whose bindings must remain in the
                    # original function so later statements can reference them.
                    if any(
                        isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        for stmt in block
                    ):
                        continue

                    span = self._block_line_span(block)
                    if span is None:
                        continue
                    start_line, end_line = span
                    line_count = end_line - start_line + 1

                    # A block that returns on some path but not on every path
                    # (a conditional return, or a lone expression statement,
                    # which counts as value-producing) is never accepted: a
                    # value-producing helper needs complete return coverage,
                    # and one that also binds live variables is rejected as
                    # mixed. Leaving such blocks out spares every pair they
                    # would have formed; on pyflakes' test_other.py that is
                    # 32,857 of 44,826 rejected pairs.
                    if is_value_producing(block) and not has_complete_return_coverage(block):
                        continue

                    if line_count >= self.min_lines:
                        results.append(((start_line, end_line), block))

            # Recurse into nested bodies for control-flow/container statements
            for stmt in body:
                # Skip nested function/class definitions to avoid crossing scopes
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue

                # Common body/orelse containers
                if hasattr(stmt, "body") and isinstance(getattr(stmt, "body"), list):
                    results.extend(extract_from_body(getattr(stmt, "body")))
                if hasattr(stmt, "orelse") and isinstance(getattr(stmt, "orelse"), list):
                    results.extend(extract_from_body(getattr(stmt, "orelse"), stmt))

                # With and AsyncWith already covered by .body
                # Try/Except/Finally blocks
                if isinstance(stmt, ast.Try):
                    if stmt.handlers:
                        for h in stmt.handlers:
                            if hasattr(h, "body") and isinstance(h.body, list):
                                results.extend(extract_from_body(h.body))
                    if hasattr(stmt, "finalbody") and isinstance(stmt.finalbody, list):
                        results.extend(extract_from_body(stmt.finalbody))

            return results

        # Prepare top-level body (skip docstring)
        body = body_without_docstring(function.body)

        return extract_from_body(body)

    def _get_used_names(self, node: ast.AST) -> Set[str]:
        """
        Get all variable names that are used (read from) in an AST node.

        This collects all Name nodes with Load context.

        Args:
            node: AST node to analyze

        Returns:
            Set of variable names that are read in the node
        """
        cached = self._used_names_cache.get(node)
        if cached is not None:
            return set(cached)

        collector = NameCollector()
        collector.visit(node)

        frozen = frozenset(collector.used)
        self._used_names_cache[node] = frozen
        return set(frozen)

    def _collect_parameter_names(self, func: FunctionNode) -> Set[str]:
        """Return all argument names for a function (including pos-only and varargs)."""

        params = set(parameter_names(func.args))

        return params

    @staticmethod
    def _enclosing_function_named(
        name: str,
        file_path: str,
        functions: FunctionIndex,
        inner: Sequence[FunctionNode],
    ) -> Optional[FunctionNode]:
        """The one function called ``name`` in ``file_path`` enclosing every ``inner`` function."""
        matches = [
            entry.node
            for entry in functions.named(file_path, name)
            if all(encloses(entry.node, function) for function in inner)
        ]
        return matches[0] if len(matches) == 1 else None

    def _deepest_common_ancestry(
        self, anc1: Optional[List[str]], anc2: Optional[List[str]]
    ) -> Optional[str]:
        """Return deepest shared symbol in two ancestry chains."""

        if not anc1 or not anc2:
            return None

        dce: Optional[str] = None
        for left, right in zip(anc1, anc2):
            if left == right:
                dce = left
            else:
                break
        return dce

    def _per_block(
        self,
        name: str,
        func: FunctionNode,
        block_nodes: Sequence[ast.AST],
        compute: Callable[[], T],
        *,
        function_id: Optional[str] = None,
        block_id: Optional[str] = None,
    ) -> T:
        """Compute an immutable per-(function, block) result once per analysis.

        The cache holds results of every analysis under one name each; the
        result type follows the ``compute`` of the name asked for.
        """
        if function_id is None:
            function_id = self._sid([func])
        if block_id is None:
            block_id = self._sid(block_nodes)
        key = (name, function_id, block_id)
        if key not in self._per_block_cache:
            self._per_block_cache[key] = compute()
        return cast(T, self._per_block_cache[key])

    def _build_block_binding_snapshot(
        self,
        func: FunctionNode,
        block_nodes: Sequence[ast.AST],
        block_range: Tuple[int, int],
        reassignments: Dict[int, bool],
        *,
        function_id: Optional[str] = None,
        block_id: Optional[str] = None,
    ) -> BlockBindingSnapshot:
        """Binding statistics for a block, computed once per (function, block)."""
        snapshot: BlockBindingSnapshot = self._per_block(
            "snapshot",
            func,
            block_nodes,
            lambda: self._compute_block_binding_snapshot(
                func, block_nodes, block_range, reassignments
            ),
            function_id=function_id,
            block_id=block_id,
        )
        return snapshot

    def _compute_block_binding_snapshot(
        self,
        func: FunctionNode,
        block_nodes: Sequence[ast.AST],
        block_range: Tuple[int, int],
        reassignments: Dict[int, bool],
    ) -> BlockBindingSnapshot:
        """Aggregate binding stats for a block."""

        bound_in_block, reassigned_in_block = _collect_block_binding_stats(
            block_nodes, reassignments
        )

        bound_before_block: Set[str] = set()
        block_start_line = block_range[0]
        for stmt in func.body:
            if hasattr(stmt, "lineno") and stmt.lineno < block_start_line:
                stmt_bound: Set[str] = set()
                stmt_reassigned: Set[str] = set()
                _collect_bindings_and_reassignments(
                    stmt, reassignments, stmt_bound, stmt_reassigned
                )
                bound_before_block.update(stmt_bound)

        bound_before_block.update(self._collect_parameter_names(func))

        bound_after_block: Set[str] = set()
        block_end_line = block_range[1]
        for stmt in func.body:
            if hasattr(stmt, "lineno") and stmt.lineno > block_end_line:
                stmt_bound = set()
                stmt_reassigned = set()
                _collect_bindings_and_reassignments(
                    stmt, reassignments, stmt_bound, stmt_reassigned
                )
                bound_after_block.update(stmt_bound)

        initially_bound = bound_in_block - bound_before_block

        return BlockBindingSnapshot(
            bound_in_block=bound_in_block,
            reassigned_in_block=reassigned_in_block,
            bound_before_block=bound_before_block,
            bound_after_block=bound_after_block,
            initially_bound=initially_bound,
        )

    def _find_return_variables(
        self,
        func: FunctionNode,
        block_range: Tuple[int, int],
        initially_bound: Set[str],
        *,
        debug_label: Optional[str] = None,
    ) -> Set[str]:
        """
        Determine which newly-bound variables are read after the block.
        """

        if not initially_bound:
            return set()

        block_end_line = block_range[1]
        result: Set[str] = set()
        debug_enabled = debugging(VALIDATION)

        for stmt in func.body:
            if not hasattr(stmt, "lineno") or stmt.lineno <= block_end_line:
                continue

            uses = self._get_used_names(stmt)
            if debug_enabled and debug_label:
                VALIDATION.debug(
                    f"  {debug_label}: stmt@{stmt.lineno} ({stmt.__class__.__name__}) uses {uses}"
                )

            overlap = uses & initially_bound
            if overlap:
                result.update(overlap)
                if debug_enabled and debug_label:
                    VALIDATION.debug(
                        f"    RETURN NEEDED ({debug_label}): Variable(s) {overlap} will be returned from extracted function"
                    )

        if debug_enabled and debug_label and result:
            VALIDATION.debug(f"{debug_label} requires returning: {result}")

        return result

    def _global_nonlocal_declarations(
        self,
        pair: CodeBlockPair,
        scope_analyzer: ScopeAnalyzer,
        free_vars: Set[str],
    ) -> Tuple[Set[str], Set[str], Set[str]]:
        """Names the helper must declare global/nonlocal, and free_vars pruned of them.

        A free variable declared global or nonlocal in the enclosing function
        cannot also be a parameter (``SyntaxError: name 'x' is parameter and
        global``), so it is dropped from free_vars and left as a free reference.
        A name assigned inside the block that is global/nonlocal but not declared
        there must be re-declared in the helper to preserve assignment semantics.
        Returns (globals_to_declare, nonlocals_to_declare, pruned_free_vars).
        """
        func1_scope_id = None
        for node, scope in scope_analyzer.node_scopes.items():
            if isinstance(node, ast.FunctionDef) and node.name == pair.function1_name:
                func1_scope_id = scope.scope_id
                break

        globals_to_declare: Set[str] = set()
        nonlocals_to_declare: Set[str] = set()

        if func1_scope_id is not None:
            global_vars = scope_analyzer.global_vars.get(func1_scope_id, set())
            nonlocal_vars = scope_analyzer.nonlocal_vars.get(func1_scope_id, set())

            # A free variable that is global/nonlocal here cannot be parameterized.
            problematic = free_vars & (global_vars | nonlocal_vars)

            # Assignment targets and explicit declarations inside the template blocks.
            v = AssignTargetVisitor()
            for n in pair.block1_nodes:
                v.visit(n)
            for n in pair.block2_nodes:
                v.visit(n)
            assigned_names = v.assigned_names
            declared_global_in_block = v.declared_global_in_block
            declared_nonlocal_in_block = v.declared_nonlocal_in_block

            # Names assigned in the block that are global/nonlocal in the enclosing
            # function must be declared in the helper to preserve assignment semantics.
            assigned_problematic_any = assigned_names & (global_vars | nonlocal_vars)
            globals_to_declare = (assigned_problematic_any & global_vars) - declared_global_in_block
            nonlocals_to_declare = (
                assigned_problematic_any & nonlocal_vars
            ) - declared_nonlocal_in_block

            # Leave problematic free variables free so the helper references the
            # outer binding rather than shadowing it with a parameter.
            free_vars = free_vars - problematic

        return globals_to_declare, nonlocals_to_declare, free_vars

    @staticmethod
    def _reserve_augassign_params(pair: CodeBlockPair, substitution: Substitution) -> Set[str]:
        """Keep augmented-assignment targets free variables rather than parameters.

        ``total += x`` reads ``total`` before writing it, so it must stay a
        parameter even when unification matched it as a substitutable expression.
        Removes those parameters from the substitution and records, per block, the
        name each maps to (for call generation). Returns the target names.
        """
        aug_finder = AugAssignFinder()
        for node in pair.block1_nodes:
            aug_finder.visit(node)
        aug_assign_vars = aug_finder.aug_assign_targets

        aug_assign_param_mappings: Dict[str, Dict[int, str]] = {}
        params_to_remove = []
        for param_name, exprs in list(substitution.param_expressions.items()):
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.Name) and expr.id in aug_assign_vars:
                    if param_name not in aug_assign_param_mappings:
                        aug_assign_param_mappings[param_name] = {}
                    aug_assign_param_mappings[param_name][block_idx] = expr.id
            if 0 in aug_assign_param_mappings.get(param_name, {}):
                params_to_remove.append(param_name)

        aug_mappings = substitution.aug_assign_mappings
        for param_name, block_mappings in aug_assign_param_mappings.items():
            if 0 in block_mappings:
                aug_mappings[block_mappings[0]] = block_mappings

        for param_name in params_to_remove:
            del substitution.param_expressions[param_name]
        return aug_assign_vars

    @staticmethod
    def _strip_fstring_params(substitution: Substitution) -> None:
        """Drop parameters that map to a whole f-string in the template block.

        Parameterizing an entire ``JoinedStr`` would replace the f-string with a
        single argument and lose its structure, so those parameters are removed.
        """
        fstring_params = []
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.JoinedStr):
                    fstring_params.append(param_name)
                    break
        for param_name in fstring_params:
            del substitution.param_expressions[param_name]

    @staticmethod
    def _working_free_vars(
        substitution: Substitution, aug_assign_vars: Set[str], free_vars1: Set[str]
    ) -> Set[str]:
        """Block1's free variables minus the ones now carried as parameters.

        A variable that became a parameter is no longer free, except an
        augmented-assignment target, which stays free so it is passed in and out.
        """
        parameterized_vars = set()
        for param_name, exprs in substitution.param_expressions.items():
            for block_idx, expr in exprs:
                if block_idx == 0 and isinstance(expr, ast.Name) and expr.id not in aug_assign_vars:
                    parameterized_vars.add(expr.id)
        return set(free_vars1) - parameterized_vars

    def _rejects_module_data_lookup(
        self,
        pair: CodeBlockPair,
        analyzer1: Optional[ScopeAnalyzer],
        analyzer2: Optional[ScopeAnalyzer],
    ) -> bool:
        """True when a block reads a module-level name that a callback could rebind.

        Module data can be rebound between two reads; passing it as a helper
        argument snapshots the value, but retaining the global name could capture
        a different caller's local. Reject until extraction can represent
        deferred, scope-correct lookups.
        """
        for block, block_analyzer in (
            (pair.block1_nodes, analyzer1),
            (pair.block2_nodes, analyzer2),
        ):
            if block_analyzer is None or block_analyzer.root_scope is None:
                continue
            for statement in block:
                for node in ast.walk(statement):
                    if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                        continue
                    binding = block_analyzer.identifier_bindings.get(
                        node
                    ) or block_analyzer.root_scope.bindings.get(node.id)
                    if (
                        binding is not None
                        and binding.scope_id == block_analyzer.root_scope.scope_id
                        and isinstance(
                            binding.node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Name)
                        )
                    ):
                        self._debug_reject(RejectReason.MODULE_DATA_LOOKUP, pair)
                        return True
        return False

    @staticmethod
    def _helper_is_trivial_forwarding(func: ast.FunctionDef) -> bool:
        """Whether the helper body is a single forwarding statement with no logic.

        A lone ``raise``, a ``return`` of a single call, or a bare call expression
        just forwards to something else, so extracting it trades a readable inline
        statement for an indirection. Size is not the signal -- a passthrough that
        forwards many arguments is verbose yet worthless -- so this matches on
        structure. Anything with real computation or more than one statement is
        left for the ordinary gates.
        """
        body = [
            statement
            for statement in func.body
            if not isinstance(statement, (ast.Global, ast.Nonlocal))
        ]
        if BlockAnalysis._helper_only_renames(body):
            return True
        if len(body) == 2:
            # ``name = call(...)`` then ``return name``, or ``a, b = call(...)``
            # then ``return (a, b)``, forwards just as a lone ``return call(...)``
            # does. Left unfiltered, two such helpers pair with each other and
            # extract a third, without end (h2 under Black, whose wrapping of
            # the call took the body over the line minimum).
            first, second = body
            if not (
                isinstance(first, ast.Assign)
                and len(first.targets) == 1
                and isinstance(first.value, ast.Call)
                and isinstance(second, ast.Return)
                and second.value is not None
            ):
                return False
            return BlockAnalysis._same_names(first.targets[0], second.value)
        if len(body) != 1:
            return False
        statement = body[0]
        if isinstance(statement, ast.Raise):
            return True
        if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            return True
        return False

    @staticmethod
    def _helper_only_renames(body: Sequence[ast.stmt]) -> bool:
        """Whether the body computes nothing: it binds names to parameters or
        literals and returns some of them.

        Such a helper turns ``a = 0; b = x`` into a call that unpacks a tuple,
        which is longer and says less (pycodestyle's initialization blocks
        once returned variables became extractable). A body with any call,
        operator, attribute, subscript, or compound statement is not this.
        """
        if not body:
            return False

        def is_plain(value: ast.expr) -> bool:
            if isinstance(value, (ast.Name, ast.Constant)):
                return True
            if isinstance(value, ast.Tuple):
                return all(is_plain(element) for element in value.elts)
            return False

        statements = list(body)
        if isinstance(statements[-1], ast.Return):
            returned = statements[-1].value
            if returned is not None and not is_plain(returned):
                return False
            statements = statements[:-1]
        if not statements:
            return False
        for statement in statements:
            if isinstance(statement, ast.Assign):
                if not is_plain(statement.value) or not all(
                    isinstance(target, (ast.Name, ast.Tuple)) for target in statement.targets
                ):
                    return False
            elif isinstance(statement, ast.AnnAssign):
                if statement.value is None or not is_plain(statement.value):
                    return False
            else:
                return False
        return True

    @staticmethod
    def _same_names(target: ast.expr, returned: ast.expr) -> bool:
        """Whether ``returned`` is exactly the name, or tuple of names, ``target`` binds."""
        if isinstance(target, ast.Name) and isinstance(returned, ast.Name):
            return target.id == returned.id
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(
            returned, (ast.Tuple, ast.List)
        ):
            return len(target.elts) == len(returned.elts) and all(
                isinstance(bound, ast.Name) and isinstance(used, ast.Name) and bound.id == used.id
                for bound, used in zip(target.elts, returned.elts)
            )
        return False
