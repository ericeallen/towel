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

"""
Unification algorithm for finding parameterizable differences in AST nodes.

This is based on Robinson's unification algorithm from automated theorem proving,
adapted for AST comparison.
"""

import ast
from typing import Callable, Dict, Optional, List, Tuple, Any, cast, Sequence, Union, Iterator

from .constant_consistency import ConstantConsistency
from .parameterization import Parameterization
from .hof_promotion import LiteralPromotion
from .substitution import Substitution
from ..diagnostics import UNIFIER


class Unifier(ConstantConsistency, Parameterization, LiteralPromotion):
    """
    Unify AST blocks to find parameterizable differences.

    This finds sub-expressions that differ between blocks and can be
    factored out into function parameters.

    Implements alpha-renaming for bound variables (loop vars, etc.).
    """

    # Track constant occurrences: (block_idx, value) -> [position_paths]
    # position_path is a tuple of (stmt_idx, field_name, ...) identifying location
    constant_positions: Dict[Tuple[int, Any], List[Tuple[Any, ...]]]

    def __init__(
        self,
        max_parameters: int = 5,
        parameterize_constants: bool = True,
        *,
        promote_equal_hof_literals: bool = False,
    ):
        """
        Initialize unifier.

        Args:
            max_parameters: Maximum number of parameters to extract
            parameterize_constants: Whether to parameterize differing constants
        """
        self.max_parameters = max_parameters
        # Feature flag: when True, enable Option B promotion of equal literals in
        # higher-order factory calls (thread as parameters even when equal).
        self._set_feature_flags(parameterize_constants, promote_equal_hof_literals)
        self.param_counter = 0
        # Track alpha-equivalence mappings for bound variables
        # Maps (block_idx, original_name) -> canonical_name
        self.alpha_renamings: Dict[Tuple[int, str], str] = {}
        # Store blocks being unified for context analysis
        self.current_blocks: Optional[Sequence[Sequence[ast.AST]]] = None
        # Track constant occurrences: (block_idx, value) -> [position_paths]
        # position_path is a tuple of (stmt_idx, field_name, ...) identifying location
        self.constant_positions = {}

    def unify_blocks(
        self, blocks: Sequence[Sequence[ast.AST]], hygienic_renames: List[Dict[str, str]]
    ) -> Optional[Substitution]:
        """
        Unify multiple code blocks.

        Args:
            blocks: List of code blocks (each is a list of AST statements)
            hygienic_renames: For each block, a mapping from original names
                             to hygienically renamed names

        Returns:
            Substitution mapping expressions to parameters, or None if unification fails
        """
        if len(blocks) < 2:
            return None

        # Check all blocks have the same number of statements
        if not all(len(b) == len(blocks[0]) for b in blocks):
            return None

        # Reset per-unification state to avoid cross-pair contamination
        # Alpha-renamings and parameter counters must start fresh for each call
        self.alpha_renamings = {}
        self._reset_unification_state(blocks)

        # Collect all constant positions for consistency checking
        self._collect_constant_positions(blocks)

        # Detect and map block-level bound variables for hygienic renaming
        # This allows unification of blocks with structurally identical code but different variable names
        self._setup_bound_variable_alpha_renamings(blocks)

        # Initialize substitution
        subst = Substitution()

        # Unify statement by statement
        for stmt_idx in range(len(blocks[0])):
            stmts = [block[stmt_idx] for block in blocks]

            # Unify this statement across all blocks
            if not self._unify_nodes(stmts, subst, list(range(len(blocks)))):
                return None

        # Check we haven't exceeded max parameters
        if len(subst.param_expressions) > self.max_parameters:
            return None

        # Copy alpha-renamings to output hygienic_renames parameter
        # This allows callers to access the renames that were applied
        for (block_idx, var_name), canonical_name in self.alpha_renamings.items():
            if block_idx < len(hygienic_renames):
                hygienic_renames[block_idx][var_name] = canonical_name

        # Also attach hygienic_renames to the substitution for downstream consumers
        # so they don't need to thread the mapping through every call.
        subst.hygienic_renames = hygienic_renames

        # After successful unification, optionally promote literal arguments in
        # higher-order factory calls (Option B policy): even if literals are
        # equal across blocks, expose them as parameters and thread through calls.
        if self.promote_equal_hof_literals:
            # Promotion mutates ``subst`` incrementally, so snapshot the exact
            # containers it can touch and restore them on failure. Otherwise a
            # mid-loop error would return a partially promoted substitution into
            # code generation. Narrow the catch to the structural errors AST
            # walking can raise so a genuine bug surfaces instead of being hidden.
            saved_mappings = dict(subst.mappings)
            saved_param_expressions = {k: list(v) for k, v in subst.param_expressions.items()}
            saved_function_params = {k: list(v) for k, v in subst.function_params.items()}
            saved_promoted = {k: dict(v) for k, v in subst.promoted_literal_args.items()}
            try:
                self._promote_hof_literals(blocks, subst)
            except (AttributeError, KeyError, TypeError, IndexError, ValueError) as error:
                UNIFIER.warning("literal promotion failed and was rolled back: %r", error)
                subst.mappings = saved_mappings
                subst.param_expressions = saved_param_expressions
                subst.function_params = saved_function_params
                subst.promoted_literal_args = saved_promoted

        return subst

    def _unify_nodes(
        self, nodes: Sequence[ast.AST], subst: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """
        Unify a list of AST nodes (one from each block).

        Args:
            nodes: List of nodes to unify
            subst: Current substitution
            block_indices: Block index for each node

        Returns:
            True if unification succeeded
        """
        # First check: do all nodes have the same type?
        node_types = [type(n) for n in nodes]
        if len(set(node_types)) != 1:
            # Different types - cannot unify at the statement level
            # Try to parameterize the entire expression
            return self._try_parameterize(nodes, subst, block_indices)

        first_node = nodes[0]

        # Special handling for different node types

        # Constants - check if they're identical, or parameterize if enabled
        if isinstance(first_node, ast.Constant):
            values = [cast(ast.Constant, n).value for n in nodes]
            if len(set(values)) == 1:
                return True  # All same constant

            # Different constants
            if self.parameterize_constants:
                # CRITICAL: Check consistency across all occurrences
                # Per the user's rule: if a constant value appears at multiple positions,
                # it must unify consistently at ALL positions.
                #
                # Example: "x = item * 2" vs "x = item * 3" AND "z = y ** 2" vs "z = y ** 2"
                # The constant 2 appears at 2 positions in block 0
                # Position 1: differs (2 vs 3)
                # Position 2: same (2 vs 2)
                # This is INCONSISTENT - we cannot parameterize just the constant 2
                #
                # Solution: Check if all occurrences of these values would unify consistently

                if not self._check_constant_consistency(values, block_indices):
                    # Constants appear at multiple positions with inconsistent unification
                    # Cannot parameterize the bare constant
                    return False

                # Parameterize differing constants
                return self._try_parameterize(nodes, subst, block_indices)
            else:
                # Cannot unify - constants must be identical
                return False

        # Names - if they differ, check alpha-renaming first
        if isinstance(first_node, ast.Name):
            # Apply alpha-renaming to get canonical names
            canonical_names = []
            for idx, (node, block_idx) in enumerate(zip(nodes, block_indices)):
                name = cast(ast.Name, node).id
                # Check if this name has an alpha-renaming for this block
                renamed = self.alpha_renamings.get((block_idx, name), name)
                canonical_names.append(renamed)

            # Check if all canonical names are the same
            if len(set(canonical_names)) == 1:
                return True  # All same name (possibly after alpha-renaming)

            # Different names even after alpha-renaming - track correspondence before parameterizing
            # Use first ORIGINAL name (not canonical) for free variable correspondence
            # This is important: we want to map admin→user, not admin→__temp_0
            original_names = [cast(ast.Name, n).id for n in nodes]
            first_original_name = original_names[0]

            for node, block_idx, original_name in zip(nodes, block_indices, original_names):
                if original_name != first_original_name:
                    # Record that this block's name maps to the first block's original name
                    # This handles free variables with different names across blocks
                    self.alpha_renamings[(block_idx, original_name)] = first_original_name

            # Now parameterize
            return self._try_parameterize(nodes, subst, block_indices)

        # For compound nodes, recursively unify all fields
        return self._unify_compound_node(nodes, subst, block_indices)

    def _unify_compound_node(
        self, nodes: Sequence[ast.AST], subst: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """
        Unify compound AST nodes by recursively unifying their fields.

        Args:
            nodes: List of nodes (all same type)
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        first_node = nodes[0]

        # Special handling for binding constructs
        # Loop variables, comprehension variables, etc. are BOUND by the construct
        # If they differ (like 'i' vs 'j'), they're alpha-equivalent, not parameterizable
        if isinstance(first_node, ast.For):
            # For loops: the target variable is bound
            # It can differ between blocks (like 'i' vs 'j') and that's OK
            # We just need to unify the structure, not the variable name
            return self._unify_for_loop(cast(List[ast.For], nodes), subst, list(block_indices))

        # Special handling for Lambda: parameters are bindings (alpha-renaming)
        # lambda x: x * 2 and lambda y: y * 2 are equivalent (alpha-equivalent)
        # The parameter names should NOT be parameterized
        if isinstance(first_node, ast.Lambda):
            return self._unify_lambda(cast(List[ast.Lambda], nodes), subst, list(block_indices))

        # Special handling for f-strings (JoinedStr)
        # F-string literal parts (Constant nodes) must NEVER be parameterized
        # Only the expressions inside FormattedValue can be parameterized
        if isinstance(first_node, ast.JoinedStr):
            return self._unify_joined_str(
                cast(List[ast.JoinedStr], nodes), subst, list(block_indices)
            )

        # Special handling for comprehensions: targets are bindings and may differ
        # Treat generator targets as alpha-equivalent like for-loop variables
        if isinstance(first_node, ast.ListComp):
            return self._unify_elt_comprehension(
                cast(List[ast.ListComp], nodes), subst, list(block_indices)
            )
        if isinstance(first_node, ast.SetComp):
            return self._unify_elt_comprehension(
                cast(List[ast.SetComp], nodes), subst, list(block_indices)
            )
        if isinstance(first_node, ast.DictComp):
            return self._unify_dict_comp(
                cast(List[ast.DictComp], nodes), subst, list(block_indices)
            )
        if isinstance(first_node, ast.GeneratorExp):
            return self._unify_elt_comprehension(
                cast(List[ast.GeneratorExp], nodes), subst, list(block_indices)
            )

        # Annotated assignments: inside a function body the annotation is
        # never evaluated, so it is neither compared nor parameterized; the
        # helper keeps the template's spelling.
        if isinstance(first_node, ast.AnnAssign):
            return self._unify_ann_assign(
                cast(List[ast.AnnAssign], nodes), subst, list(block_indices)
            )

        # Special handling for with-statements: optional_vars are bindings
        if isinstance(first_node, ast.With):
            return self._unify_with(cast(List[ast.With], nodes), subst, list(block_indices))

        # Special handling for except handlers: name is a binding identifier
        if isinstance(first_node, ast.ExceptHandler):
            return self._unify_except_handler(
                cast(List[ast.ExceptHandler], nodes), subst, list(block_indices)
            )

        # Special handling for walrus operator: target is a binding (alpha-equivalent)
        if isinstance(first_node, ast.NamedExpr):
            return self._unify_named_expr(
                cast(List[ast.NamedExpr], nodes), subst, list(block_indices)
            )

        # For each field in the node
        for field_name in first_node._fields:
            # Skip location fields
            if field_name in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
                continue

            # Get field values from all nodes
            field_values = [getattr(n, field_name, None) for n in nodes]

            first_value = field_values[0]

            # Handle different value types
            if first_value is None:
                # All None - OK
                if not all(v is None for v in field_values):
                    # Some None, some not - can't unify
                    return False
                continue

            elif isinstance(first_value, list):
                # Lists of AST nodes or primitives
                # Check all are lists
                if not all(isinstance(v, list) for v in field_values):
                    # Mixed list/non-list - can't unify
                    return False
                if not self._unify_lists(
                    cast(Sequence[Sequence[Any]], field_values), subst, block_indices
                ):
                    return False

            elif isinstance(first_value, ast.AST):
                # Single AST node(s)
                # Check all are AST nodes
                if not all(isinstance(v, ast.AST) for v in field_values):
                    # Mixed AST/non-AST - can't unify
                    return False

                # Special handling for operator nodes (no fields - just type markers)
                # Operators like Add, Sub, Not, etc. define the operation semantics
                # and should NOT be parameterized - they must be identical
                if len(first_value._fields) == 0:
                    # Operator node - all must have same type
                    value_types = set(type(v) for v in field_values)
                    if len(value_types) > 1:
                        # Different operators - can't unify
                        return False
                    # Same operator type - continue
                    continue

                # Regular AST nodes - _unify_nodes handles type differences via parameterization
                if not self._unify_nodes(
                    cast(Sequence[ast.AST], field_values), subst, block_indices
                ):
                    return False

            else:
                # Primitive value (string, int, etc.)
                # Check all are non-AST, non-list primitives
                if any(isinstance(v, (ast.AST, list)) for v in field_values):
                    # Mixed primitive/AST or primitive/list - can't unify
                    return False
                # Must all be equal
                if not all(v == first_value for v in field_values):
                    # Try to parameterize the entire node if primitives differ
                    return self._try_parameterize(nodes, subst, block_indices)

        return True

    def _unify_elt_comprehension(
        self,
        nodes: Sequence[Union[ast.ListComp, ast.SetComp, ast.GeneratorExp]],
        subst: Substitution,
        block_indices: List[int],
    ) -> bool:
        """Unify comprehensions that produce one element, with alpha-renamed generator targets.

        Variables bound in each generator's target are bindings (like loop
        variables) and may differ across blocks: temporary alpha-renamings use
        the first block as canonical, every generator unifies under them, and
        then the element does.
        """
        if not nodes:
            return False
        return self._unify_comprehension_core(
            nodes,
            subst,
            block_indices,
            lambda: self._unify_nodes([n.elt for n in nodes], subst, block_indices),
        )

    def _unify_comprehension_core(
        self,
        nodes: Sequence[Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]],
        subst: Substitution,
        block_indices: List[int],
        finalize: Callable[[], bool],
    ) -> bool:
        gen_lists = [n.generators for n in nodes]
        if not gen_lists or not all(len(g) == len(gen_lists[0]) for g in gen_lists):
            return False

        saved_alpha = dict(self.alpha_renamings)
        try:
            num_gens = len(gen_lists[0])
            for gen_idx in range(num_gens):
                comps = [g[gen_idx] for g in gen_lists]
                if not self._unify_single_comprehension(comps, subst, block_indices):
                    return False
            return finalize()
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_dict_comp(
        self, nodes: List[ast.DictComp], subst: Substitution, block_indices: List[int]
    ) -> bool:
        if not nodes:
            return False

        return self._unify_comprehension_core(
            nodes,
            subst,
            block_indices,
            lambda: self._unify_nodes([n.key for n in nodes], subst, block_indices)
            and self._unify_nodes([n.value for n in nodes], subst, block_indices),
        )

    def _unify_with(
        self, nodes: List[ast.With], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify With nodes, treating optional_vars as bound (alpha-equivalent).

        - Unify items' context_expr.
        - Establish temporary alpha-renamings for optional_vars targets.
        - Unify bodies under those mappings.
        """
        # Same number of items
        items_lists = [n.items for n in nodes]
        if not all(len(lst) == len(items_lists[0]) for lst in items_lists):
            return False

        # Save mappings to restore after
        saved_alpha = dict(self.alpha_renamings)
        try:
            # Unify each item
            num_items = len(items_lists[0])
            for i in range(num_items):
                items_i = [lst[i] for lst in items_lists]
                # Unify context_expr
                if not self._unify_nodes([it.context_expr for it in items_i], subst, block_indices):
                    return False

                # Handle optional_vars as bindings
                optional_vars_raw = [it.optional_vars for it in items_i]
                if all(ov is None for ov in optional_vars_raw):
                    pass  # nothing to do
                else:
                    # All must be AST; if any None while others not, fail
                    if any(ov is None for ov in optional_vars_raw):
                        return False
                    # Establish alpha-renaming for targets
                    targets = cast(List[ast.expr], optional_vars_raw)
                    # Support simple Name or Tuple[List] of Names
                    # Collect names positionally

                    def flatten_names(t: ast.AST) -> List[str]:
                        if isinstance(t, ast.Name):
                            return [t.id]
                        if isinstance(t, (ast.Tuple, ast.List)):
                            names: List[str] = []
                            for e in t.elts:
                                names.extend(flatten_names(e))
                            return names
                        return []

                    names_per_block = [flatten_names(t) for t in targets]
                    # Ensure all have same arity
                    arities = [len(nl) for nl in names_per_block]
                    if len(set(arities)) != 1:
                        return False
                    # Use first block's names as canonical, map positionally
                    for pos in range(arities[0]):
                        canonical = names_per_block[0][pos]
                        for idx, block_idx in enumerate(block_indices):
                            actual = names_per_block[idx][pos]
                            self.alpha_renamings[(block_idx, actual)] = canonical

            # Unify bodies
            if not self._unify_lists([n.body for n in nodes], subst, block_indices):
                return False

            # type_comment (if present) must match
            comments = [getattr(n, "type_comment", None) for n in nodes]
            if not all(c == comments[0] for c in comments):
                return False

            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_except_handler(
        self, nodes: List[ast.ExceptHandler], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify ExceptHandler nodes, treating the 'name' as a bound identifier.
        """
        # Unify exception types (may be None)
        types_list: List[Optional[ast.expr]] = [n.type for n in nodes]
        if all(t is None for t in types_list):
            pass
        else:
            if any(t is None for t in types_list):
                return False
            if not self._unify_nodes(cast(List[ast.AST], types_list), subst, block_indices):
                return False

        # Establish temporary alpha-renamings for the handler variable names (strings)
        saved_alpha = dict(self.alpha_renamings)
        try:
            names: List[Optional[str]] = [n.name for n in nodes]
            # If all None, fine; if some None and others not, fail
            if all(nm is None for nm in names):
                pass
            else:
                if not all((nm is None) == (names[0] is None) for nm in names):
                    return False
                if names[0] is not None:
                    names_str: List[str] = cast(List[str], names)
                    canonical = names_str[0]
                    for idx, block_idx in enumerate(block_indices):
                        actual = names_str[idx]
                        self.alpha_renamings[(block_idx, actual)] = canonical

            # Unify body
            if not self._unify_lists([n.body for n in nodes], subst, block_indices):
                return False
            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_named_expr(
        self, nodes: List[ast.NamedExpr], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify walrus (NamedExpr) treating the target as a binding.
        Only simple Name targets are recognized for alpha-renaming.
        """
        # Save and set alpha mappings for targets
        saved_alpha = dict(self.alpha_renamings)
        try:
            targets = [n.target for n in nodes]
            name_targets: List[ast.Name] = [t for t in targets if isinstance(t, ast.Name)]
            if len(name_targets) == len(targets):
                names = [t.id for t in name_targets]
                canonical = names[0]
                for idx, block_idx in enumerate(block_indices):
                    self.alpha_renamings[(block_idx, names[idx])] = canonical

            # Unify values under established alpha-renamings
            if not self._unify_nodes([n.value for n in nodes], subst, block_indices):
                return False
            # Do not require exact match for target identifiers (treated as bindings)
            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_ann_assign(
        self, nodes: List[ast.AnnAssign], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """Unify annotated assignments by target and value, ignoring the annotation."""
        if not all(isinstance(node, ast.AnnAssign) for node in nodes):
            return False
        if len({node.simple for node in nodes}) != 1:
            return False
        values = [node.value for node in nodes]
        if any(value is None for value in values):
            return all(value is None for value in values) and self._unify_nodes(
                [node.target for node in nodes], subst, block_indices
            )
        return self._unify_nodes(
            cast(List[ast.AST], values), subst, block_indices
        ) and self._unify_nodes([node.target for node in nodes], subst, block_indices)

    def _unify_single_comprehension(
        self, comps: List[ast.comprehension], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify a single 'comprehension' node across blocks, establishing
        alpha-renamings for its target (Name or Tuple of Names), then unifying
        its iterator and if-clauses under those mappings.
        """
        # All must be comprehension nodes
        if not all(isinstance(c, ast.comprehension) for c in comps):
            return False

        # is_async flags must match
        async_flags = [c.is_async for c in comps]
        if len(set(async_flags)) != 1:
            return False

        # Handle targets as bindings
        targets = [c.target for c in comps]

        # Simple Name targets
        if all(isinstance(t, ast.Name) for t in targets):
            name_targets = cast(List[ast.Name], targets)
            names = [t.id for t in name_targets]
            canonical = names[0]
            # Establish alpha-renaming for the duration of the entire comprehension
            for idx, block_idx in enumerate(block_indices):
                key = (block_idx, names[idx])
                self.alpha_renamings[key] = canonical

            # Unify iterator and ifs under alpha-renaming
            if not self._unify_nodes([c.iter for c in comps], subst, block_indices):
                return False
            if not self._unify_lists([c.ifs for c in comps], subst, block_indices):
                return False
            return True

        # Tuple targets with simple names
        if all(isinstance(t, ast.Tuple) for t in targets):
            # All tuples must be flat and same length with Name elts
            tuple_targets = cast(List[ast.Tuple], targets)
            lengths = [len(t.elts) for t in tuple_targets]
            if len(set(lengths)) != 1:
                return False
            if not all(all(isinstance(e, ast.Name) for e in t.elts) for t in tuple_targets):
                return False

            tuple_names: List[List[str]] = []  # per position names
            for pos in range(lengths[0]):
                # We verified above that all elements are ast.Name, so cast for type checker
                tuple_names.append([cast(ast.Name, t.elts[pos]).id for t in tuple_targets])

            # Establish alpha-renaming per position to the first block's names
            for pos, names_at_pos in enumerate(tuple_names):
                canonical = names_at_pos[0]
                for idx, block_idx in enumerate(block_indices):
                    key = (block_idx, names_at_pos[idx])
                    self.alpha_renamings[key] = canonical

            # Unify iterator and ifs
            if not self._unify_nodes([c.iter for c in comps], subst, block_indices):
                return False
            if not self._unify_lists([c.ifs for c in comps], subst, block_indices):
                return False
            return True

        # Fallback: complex targets - unify structurally without alpha-renaming
        return (
            self._unify_nodes([c.target for c in comps], subst, block_indices)
            and self._unify_nodes([c.iter for c in comps], subst, block_indices)
            and self._unify_lists([c.ifs for c in comps], subst, block_indices)
        )

    def _unify_loop_components(
        self,
        nodes: Sequence[ast.For],
        subst: Substitution,
        block_indices: Sequence[int],
    ) -> bool:
        return (
            self._unify_nodes([n.iter for n in nodes], subst, block_indices)
            and self._unify_lists([n.body for n in nodes], subst, block_indices)
            and self._unify_lists([n.orelse for n in nodes], subst, block_indices)
        )

    def _unify_for_loop(
        self, nodes: List[ast.For], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify For loops, treating loop variables as bound (alpha-equivalent).

        Args:
            nodes: List of For nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # For loops have: target, iter, body, orelse
        # The 'target' is a bound variable - it can differ (i vs j) and that's OK

        # Get the loop variable names
        targets = [n.target for n in nodes]

        # Check if targets are simple Names or Tuples
        if not all(isinstance(t, ast.Name) for t in targets):
            # Check if all targets are tuples (for tuple unpacking support)
            if all(isinstance(t, ast.Tuple) for t in targets):
                # Delegate to tuple unpacking handler
                return self._unify_for_loop_with_tuple_targets(nodes, subst, block_indices)

            # Complex targets (nested structures, etc.) - fall back to default unification
            return (
                self._unify_nodes(targets, subst, block_indices)
                and self._unify_nodes([n.iter for n in nodes], subst, block_indices)
                and self._unify_lists([n.body for n in nodes], subst, block_indices)
                and self._unify_lists([n.orelse for n in nodes], subst, block_indices)
            )

        name_targets = cast(List[ast.Name], targets)
        loop_var_names = [t.id for t in name_targets]

        # Check if all loop variables have the same name
        if len(set(loop_var_names)) == 1:
            # Same loop variable name - just unify normally
            return self._unify_loop_components(nodes, subst, block_indices)

        # Different loop variable names (i vs j) - establish alpha-equivalence
        # Use the first block's variable name as canonical
        canonical_var = loop_var_names[0]

        # Establish alpha-renaming mappings for all blocks
        # Save old mappings to restore later
        old_mappings: Dict[Tuple[int, str], str] = {}
        for idx, block_idx in enumerate(block_indices):
            var_name = loop_var_names[idx]
            key = (block_idx, var_name)
            self._assign_alpha_mapping(key, canonical_var, old_mappings)

        try:
            # Unify the iterator
            return self._unify_loop_components(nodes, subst, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for idx, block_idx in enumerate(block_indices):
                var_name = loop_var_names[idx]
                key = (block_idx, var_name)
                self._restore_alpha_mapping(key, old_mappings)

    def _unify_for_loop_with_tuple_targets(
        self, nodes: List[ast.For], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify For loops with tuple unpacking targets (e.g., for key, value in items).

        Handles flat tuple unpacking with alpha-renaming:
        - for key, value in pairs
        - for k, v in pairs

        Args:
            nodes: List of For nodes with Tuple targets
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        targets = [n.target for n in nodes]

        # Verify all targets are tuples
        if not all(isinstance(t, ast.Tuple) for t in targets):
            return False

        # Check all tuples have the same number of elements
        tuple_targets = cast(List[ast.Tuple], targets)
        tuple_lengths = [len(t.elts) for t in tuple_targets]
        if len(set(tuple_lengths)) != 1:
            return False

        # Check all tuple elements are simple Name nodes
        for target in tuple_targets:
            if not all(isinstance(elt, ast.Name) for elt in target.elts):
                return False

        # Extract variable names for each position
        # var_names[position_idx] = [name_in_block0, name_in_block1, ...]
        num_positions = len(tuple_targets[0].elts)
        var_names = []
        for pos in range(num_positions):
            # We verified elements are ast.Name above; cast to satisfy type checker
            names_at_pos = [cast(ast.Name, target.elts[pos]).id for target in tuple_targets]
            var_names.append(names_at_pos)

        # Check if all corresponding names are identical
        # If so, no alpha-renaming needed
        all_same = all(len(set(names)) == 1 for names in var_names)

        if all_same:
            # All tuple unpacking uses same variable names - just unify normally
            return self._unify_loop_components(nodes, subst, block_indices)

        # Different variable names - establish alpha-equivalence for each position
        # Use the first block's variable names as canonical
        canonical_vars = [names[0] for names in var_names]

        # Establish alpha-renaming mappings for all positions and blocks
        # Save old mappings to restore later
        old_mappings: Dict[Tuple[int, str], str] = {}
        for pos_idx, canonical_var in enumerate(canonical_vars):
            for idx, block_idx in enumerate(block_indices):
                var_name = var_names[pos_idx][idx]
                key = (block_idx, var_name)
                self._assign_alpha_mapping(key, canonical_var, old_mappings)

        try:
            # Unify the iterator
            return self._unify_loop_components(nodes, subst, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for pos_idx, canonical_var in enumerate(canonical_vars):
                for idx, block_idx in enumerate(block_indices):
                    var_name = var_names[pos_idx][idx]
                    key = (block_idx, var_name)
                    self._restore_alpha_mapping(key, old_mappings)

    def _unify_lambda(
        self, nodes: List[ast.Lambda], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify lambda expressions with alpha-renaming support.

        Lambda parameters are bindings, similar to for loop variables.
        If they differ (like 'lambda x: ...' vs 'lambda y: ...'), they're
        alpha-equivalent, not parameterizable.

        Args:
            nodes: List of Lambda nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # For now, only handle simple case: same number of regular positional args
        # Get parameter counts for each lambda
        param_counts = [len(n.args.args) for n in nodes]
        if len(set(param_counts)) > 1:
            # Different number of parameters - can't unify
            return False

        # Check that all other parameter types are empty (no *args, **kwargs, etc.)
        for node in nodes:
            if node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
                # Complex lambda parameters - not currently supported
                # See docs/KNOWN_LIMITATIONS.md for details
                return False

        # Get parameter names from each lambda
        num_params = param_counts[0]
        if num_params == 0:
            # No parameters - just unify bodies directly
            return self._unify_nodes([n.body for n in nodes], subst, block_indices)

        # Create alpha-renaming mappings for lambda parameters
        # Use first lambda's parameter names as canonical
        canonical_params = [nodes[0].args.args[i].arg for i in range(num_params)]

        # Save old alpha-renaming mappings (in case of nested lambdas)
        old_mappings: Dict[Tuple[int, str], str] = {}
        try:
            # Set up alpha-renamings for each parameter position
            for param_idx in range(num_params):
                canonical_param = canonical_params[param_idx]
                for idx, node in enumerate(nodes):
                    block_idx = block_indices[idx]
                    actual_param = node.args.args[param_idx].arg
                    key = (block_idx, actual_param)
                    self._assign_alpha_mapping(key, canonical_param, old_mappings)

            # Unify lambda bodies with alpha-renaming in effect
            return self._unify_nodes([n.body for n in nodes], subst, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for param_idx in range(num_params):
                for idx, node in enumerate(nodes):
                    block_idx = block_indices[idx]
                    actual_param = node.args.args[param_idx].arg
                    key = (block_idx, actual_param)
                    self._restore_alpha_mapping(key, old_mappings)

    def _unify_joined_str(
        self, nodes: List[ast.JoinedStr], subst: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify f-strings (JoinedStr), never parameterizing Constant children.

        F-strings have strict structure requirements:
        - values list can only contain Constant or FormattedValue nodes
        - Constant nodes are string literals and must NEVER be parameterized
        - FormattedValue nodes contain expressions that CAN be unified/parameterized

        Args:
            nodes: List of JoinedStr nodes
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # Check all have the same number of values
        values_lists = [n.values for n in nodes]
        if not all(len(v) == len(values_lists[0]) for v in values_lists):
            # Different number of components - can't unify
            return False

        # Unify each component
        for i in range(len(values_lists[0])):
            components = [values[i] for values in values_lists]

            # Check all components are the same type
            component_types = [type(c) for c in components]
            if len(set(component_types)) != 1:
                # Different types at this position - can't unify
                return False

            first_component = components[0]

            if isinstance(first_component, ast.Constant):
                # String literal parts MUST be identical - NEVER parameterize
                const_components = cast(List[ast.Constant], components)
                values = [c.value for c in const_components]
                if not all(v == values[0] for v in values):
                    # Different string literals - can't unify f-strings with different text
                    return False

            elif isinstance(first_component, ast.FormattedValue):
                # FormattedValue contains an expression - unify it normally
                # Extract the value expressions
                fmt_components = cast(List[ast.FormattedValue], components)
                value_exprs = [c.value for c in fmt_components]
                if not self._unify_nodes(value_exprs, subst, block_indices):
                    return False

                # Also check conversion and format_spec if present
                conversions = [c.conversion for c in fmt_components]
                if not all(conv == conversions[0] for conv in conversions):
                    return False

                # format_spec can be None or another JoinedStr
                format_specs = [c.format_spec for c in fmt_components]
                if format_specs[0] is not None:
                    if not all(fs is not None for fs in format_specs):
                        return False
                    if isinstance(format_specs[0], ast.JoinedStr):
                        joined_specs = cast(List[ast.JoinedStr], format_specs)
                        if not self._unify_joined_str(joined_specs, subst, block_indices):
                            return False

            else:
                # Unexpected component type
                return False

        return True

    def _unify_lists(
        self, lists: Sequence[Sequence[Any]], subst: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """
        Unify lists of values.

        Args:
            lists: List of lists to unify
            subst: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # All lists must have same length
        if not all(len(lst) == len(lists[0]) for lst in lists):
            return False

        # Unify element by element
        for i in range(len(lists[0])):
            elements = [lst[i] for lst in lists]

            # Check element types
            elem_types = set(type(e) for e in elements)
            if len(elem_types) > 1:
                return False

            first_elem = elements[0]

            if isinstance(first_elem, ast.AST):
                # AST nodes - unify recursively
                if not self._unify_nodes(elements, subst, block_indices):
                    return False
            elif isinstance(first_elem, list):
                # Nested lists
                if not self._unify_lists(elements, subst, block_indices):
                    return False
            else:
                # Primitive values - must be equal
                if not all(e == first_elem for e in elements):
                    return False

        return True

    @staticmethod
    def _iter_child_fields(node: ast.AST) -> Iterator[Tuple[str, Any]]:
        """Yield (field_name, value) pairs skipping location metadata fields."""
        for field_name in getattr(node, "_fields", ()):  # pragma: no branch - simple iteration
            if field_name in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
                continue
            yield field_name, getattr(node, field_name, None)

    def _set_feature_flags(
        self, parameterize_constants: bool, promote_equal_hof_literals: bool
    ) -> None:
        self.parameterize_constants = parameterize_constants
        self.promote_equal_hof_literals = promote_equal_hof_literals

    def _reset_unification_state(self, blocks: Sequence[Sequence[ast.AST]]) -> None:
        self.param_counter = 0
        self.current_blocks = blocks
        # A helper extracted on an earlier pass already binds names such as
        # ``__param_0``; a fresh parameter must not alias any identifier the
        # blocks mention, or the substituted body becomes ambiguous.
        self._reserved_parameter_names = {
            node.id
            for block in blocks
            for statement in block
            for node in ast.walk(statement)
            if isinstance(node, ast.Name)
        }
