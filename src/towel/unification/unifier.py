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

"""Anti-unification of code blocks into one helper template.

Two (or more) blocks are compared node by node; where they agree the
template keeps the node, and where they differ in an expression the
template takes a fresh parameter that each block instantiates with its own
expression. Bound names (loop targets, lambda parameters, comprehension
targets, with-items, handler names, walrus targets) are alpha-equivalent,
never parameterized, and so is whatever a tool reads where it stands (see
``static_positions``). This is Plotkin and Reynolds's anti-unification (the
least general generalization), not Robinson's unification, which solves
for a substitution making two terms equal.
"""

import ast
from typing import Callable, Dict, Optional, List, Tuple, Any, Sequence, Type, Union, Iterator

from .constant_consistency import ConstantConsistency, constant_identity
from .parameterization import Parameterization
from .hof_promotion import LiteralPromotion
from .statement_facts import mentioned_names
from .static_positions import (
    DEFAULT_TRANSLATION_KEYWORDS,
    TYPING_FORMS_BY_NAME,
    TranslationKeywords,
    TypingForms,
    statically_read,
)
from .substitution import Substitution, structural_text
from .unifier_state import ConstantIdentity
from .visitors import all_instances

_REPEATING = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.comprehension,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Lambda,
)
"""Constructs whose parts may run more than once each time the statement holding them runs."""


def _dotted_chain(expression: ast.AST) -> Optional[Tuple[ast.Name, List[str]]]:
    """``a.b.c`` as its root name and attribute names, or None for anything else."""
    attributes: List[str] = []
    while isinstance(expression, ast.Attribute):
        attributes.append(expression.attr)
        expression = expression.value
    if not isinstance(expression, ast.Name):
        return None
    return expression, attributes[::-1]


class Unifier(ConstantConsistency, Parameterization, LiteralPromotion):
    """
    Unify AST blocks to find parameterizable differences.

    This finds sub-expressions that differ between blocks and can be
    factored out into function parameters.

    Implements alpha-renaming for bound variables (loop vars, etc.).
    """

    constant_positions: Dict[Tuple[int, ConstantIdentity], List[Tuple[object, ...]]]

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
            promote_equal_hof_literals: Thread equal literals in higher-order
                factory calls as parameters too, instead of leaving them inline
        """
        self.max_parameters = max_parameters
        # Feature flag: when True, enable Option B promotion of equal literals in
        # higher-order factory calls (thread as parameters even when equal).
        self._set_feature_flags(parameterize_constants, promote_equal_hof_literals)
        self.param_counter = 0
        self.alpha_renamings: Dict[Tuple[int, str], str] = {}
        self.current_blocks: Optional[Sequence[Sequence[ast.AST]]] = None
        self.constant_positions = {}
        self._pattern_depth = 0
        self._pattern_parameters_allowed = False
        self._read_in_place = ()

    def unify_blocks(
        self,
        blocks: Sequence[Sequence[ast.AST]],
        hygienic_renames: List[Dict[str, str]],
        *,
        translation_keywords: TranslationKeywords = DEFAULT_TRANSLATION_KEYWORDS,
        typing_forms: Optional[Sequence[TypingForms]] = None,
    ) -> Optional[Substitution]:
        """
        Unify multiple code blocks.

        Args:
            blocks: List of code blocks (each is a list of AST statements)
            hygienic_renames: For each block, a mapping from original names
                             to hygienically renamed names
            translation_keywords: The markers whose messages extraction reads,
                             which the blocks may not differ in
            typing_forms: For each block, what its callees denote among the
                             typing forms where its module binds them; without
                             them every callee is the form its name spells

        Returns:
            Substitution mapping expressions to parameters, or None if unification fails
        """
        if len(blocks) < 2:
            return None

        if not all(len(b) == len(blocks[0]) for b in blocks):
            return None

        # Reset per-unification state to avoid cross-pair contamination
        # Alpha-renamings and parameter counters must start fresh for each call
        self.alpha_renamings = {}
        self._reset_unification_state(blocks, translation_keywords, typing_forms)

        self._collect_constant_positions(blocks)

        # Detect and map block-level bound variables for hygienic renaming
        # This allows unification of blocks with structurally identical code but different variable names
        self._setup_bound_variable_alpha_renamings(blocks)

        substitution = Substitution()

        # Unify statement by statement
        for stmt_idx in range(len(blocks[0])):
            stmts = [block[stmt_idx] for block in blocks]

            # Unify this statement across all blocks
            if not self._unify_nodes(stmts, substitution, list(range(len(blocks)))):
                return None

        if len(substitution.param_expressions) > self.max_parameters:
            return None

        # Copy alpha-renamings to output hygienic_renames parameter
        # This allows callers to access the renames that were applied
        for (block_idx, var_name), canonical_name in self.alpha_renamings.items():
            if block_idx < len(hygienic_renames):
                hygienic_renames[block_idx][var_name] = canonical_name

        # Also attach hygienic_renames to the substitution for downstream consumers
        # so they don't need to thread the mapping through every call.
        substitution.hygienic_renames = hygienic_renames

        # After successful unification, optionally promote literal arguments in
        # higher-order factory calls (Option B policy): even if literals are
        # equal across blocks, expose them as parameters and thread through calls.
        if self.promote_equal_hof_literals:
            self._promote_hof_literals(blocks, substitution)

        return substitution

    def _unify_nodes(
        self, nodes: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """
        Unify a list of AST nodes (one from each block).

        Args:
            nodes: List of nodes to unify
            substitution: Current substitution
            block_indices: Block index for each node

        Returns:
            True if unification succeeded
        """
        # First check: do all nodes have the same type?
        node_types = [type(n) for n in nodes]
        if len(set(node_types)) != 1:
            # Different types - cannot unify at the statement level
            return self._try_parameterize(nodes, substitution, block_indices)

        # Constants - check if they're identical, or parameterize if enabled.
        # Identical means the same type as well as an equal value: 0, 0.0 and
        # False are equal, and a helper keeping one would return it for all.
        if all_instances(nodes, ast.Constant):
            values = [n.value for n in nodes]
            if len({constant_identity(value) for value in values}) == 1:
                return True  # All same constant

            if self.parameterize_constants:
                # Invariant: a constant value is parameterized at every position
                # it occupies or at none. With ``x = item * 2`` against
                # ``x = item * 3`` and ``z = y ** 2`` in both, the 2 differs at one
                # position and agrees at the other, so it cannot become a parameter.
                if not self._check_constant_consistency(values, block_indices):
                    return False
                return self._try_parameterize(nodes, substitution, block_indices)
            else:
                # Cannot unify - constants must be identical
                return False

        # Names - if they differ, check alpha-renaming first
        if all_instances(nodes, ast.Name):
            canonical_names = []
            for node, block_idx in zip(nodes, block_indices):
                name = node.id
                renamed = self.alpha_renamings.get((block_idx, name), name)
                canonical_names.append(renamed)

            if len(set(canonical_names)) == 1:
                return True  # All same name (possibly after alpha-renaming)

            # Different names even after alpha-renaming - track correspondence before parameterizing
            # Use first ORIGINAL name (not canonical) for free variable correspondence
            # This is important: we want to map admin→user, not admin→__temp_0
            original_names = [n.id for n in nodes]
            first_original_name = original_names[0]

            for node, block_idx, original_name in zip(nodes, block_indices, original_names):
                if original_name != first_original_name:
                    # Record that this block's name maps to the first block's original name
                    # This handles free variables with different names across blocks
                    self.alpha_renamings[(block_idx, original_name)] = first_original_name

            # Now parameterize
            return self._try_parameterize(nodes, substitution, block_indices)

        # For compound nodes, recursively unify all fields
        return self._unify_compound_node(nodes, substitution, block_indices)

    #: Node types whose bound names are alpha-equivalent, and the method that unifies each.
    def _unify_compound_node(
        self, nodes: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """
        Unify compound AST nodes by recursively unifying their fields.

        Args:
            nodes: List of nodes (all same type)
            substitution: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        first_node = nodes[0]

        # A construct that binds names (loop targets, lambda parameters,
        # comprehension targets, with-items, handler names, walrus targets)
        # is unified by a method of its own: the bound names are
        # alpha-equivalent, never parameterized, and f-string literal parts
        # and annotations are compared, never parameterized, likewise.
        handler = self._BINDING_CONSTRUCT_UNIFIERS.get(type(first_node))
        if handler is not None:
            return handler(self, nodes, substitution, list(block_indices))
        if isinstance(first_node, ast.pattern):
            return self._unify_pattern(nodes, substitution, block_indices)
        return self._unify_fields(nodes, substitution, block_indices)

    def _unify_pattern(
        self, nodes: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """Unify match patterns, where only the names of classes and namespaces may differ.

        A pattern is not an expression, and most of it cannot stand for a
        parameter: where a pattern expects a value, a bare name is a capture
        that matches anything and binds it (``case [1, x]`` would become
        ``case [__param_0, x]``, ``case Color.RED`` a capture or a class
        pattern), and a mapping key must be a literal or a dotted name. A
        name keeps its meaning in two places only: as the class of a class
        pattern (``case __param_0():``) and at the root of a dotted name
        (``case __param_0.RED:``), and only while the argument is passed by
        value; one passed as a thunk puts a call in the pattern, which does
        not parse, and the rendered proposal is dropped.
        """
        self._pattern_depth += 1
        try:
            first = nodes[0]
            if isinstance(first, ast.MatchValue) and all_instances(nodes, ast.MatchValue):
                return self._unify_pattern_value(
                    [node.value for node in nodes], substitution, block_indices
                )
            if isinstance(first, ast.MatchClass) and all_instances(nodes, ast.MatchClass):
                return (
                    self._unify_pattern_class(
                        [node.cls for node in nodes], substitution, block_indices
                    )
                    and all(node.kwd_attrs == first.kwd_attrs for node in nodes)
                    and self._unify_lists(
                        [node.patterns for node in nodes], substitution, block_indices
                    )
                    and self._unify_lists(
                        [node.kwd_patterns for node in nodes], substitution, block_indices
                    )
                )
            if isinstance(first, ast.MatchMapping) and all_instances(nodes, ast.MatchMapping):
                key_lists = [node.keys for node in nodes]
                if any(len(keys) != len(first.keys) for keys in key_lists):
                    return False
                return (
                    all(
                        self._unify_pattern_value(
                            [keys[index] for keys in key_lists], substitution, block_indices
                        )
                        for index in range(len(first.keys))
                    )
                    and all(node.rest == first.rest for node in nodes)
                    and self._unify_lists(
                        [node.patterns for node in nodes], substitution, block_indices
                    )
                )
            return self._unify_fields(nodes, substitution, block_indices)
        finally:
            self._pattern_depth -= 1

    def _unify_pattern_value(
        self, values: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """A value pattern's literal or dotted name, or a mapping key: only a dotted name's root may differ."""
        chains = [_dotted_chain(value) for value in values]
        if any(chain is None for chain in chains):
            return self._unify_nodes(values, substitution, block_indices)
        attributes = {tuple(chain[1]) for chain in chains if chain is not None}
        if len(attributes) != 1:
            return self._unify_nodes(values, substitution, block_indices)
        roots = [chain[0] for chain in chains if chain is not None]
        return self._unify_with_pattern_parameters(roots, substitution, block_indices)

    def _unify_pattern_class(
        self, classes: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """A class pattern's class: a name or dotted name, which may differ as a whole."""
        return self._unify_with_pattern_parameters(classes, substitution, block_indices)

    def _unify_with_pattern_parameters(
        self, nodes: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """Unify expressions a pattern holds where a parameter's bare name keeps its meaning."""
        allowed = self._pattern_parameters_allowed
        self._pattern_parameters_allowed = True
        try:
            return self._unify_nodes(nodes, substitution, block_indices)
        finally:
            self._pattern_parameters_allowed = allowed

    def _try_parameterize(
        self, exprs: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """Parameterize differing expressions; inside a pattern, only where ``_unify_pattern`` allows."""
        if self._pattern_depth and not self._pattern_parameters_allowed:
            return False
        return super()._try_parameterize(exprs, substitution, block_indices)

    def _unify_fields(
        self, nodes: Sequence[ast.AST], substitution: Substitution, block_indices: Sequence[int]
    ) -> bool:
        """Unify nodes of one type field by field, parameterizing where expressions differ."""
        first_node = nodes[0]
        for field_name in first_node._fields:
            if field_name in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
                continue

            field_values = [getattr(n, field_name, None) for n in nodes]

            first_value = field_values[0]

            if first_value is None:
                # All None - OK
                if not all(v is None for v in field_values):
                    # Some None, some not - can't unify
                    return False
                continue

            elif isinstance(first_value, list):
                # Lists of AST nodes or primitives
                if not all_instances(field_values, list) or not self._unify_lists(
                    field_values, substitution, block_indices
                ):
                    return False

            elif isinstance(first_value, ast.AST):
                # Single AST node(s)
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
                if not all_instances(field_values, ast.AST) or not self._unify_nodes(
                    field_values, substitution, block_indices
                ):
                    return False

            else:
                # Primitive value (string, int, etc.)
                if any(isinstance(v, (ast.AST, list)) for v in field_values):
                    # Mixed primitive/AST or primitive/list - can't unify
                    return False
                # Must all be equal
                if not all(v == first_value for v in field_values):
                    return self._try_parameterize(nodes, substitution, block_indices)

        return True

    def _unify_elt_comprehension(
        self,
        nodes: Sequence[Union[ast.ListComp, ast.SetComp, ast.GeneratorExp]],
        substitution: Substitution,
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
            substitution,
            block_indices,
            lambda: self._unify_nodes([n.elt for n in nodes], substitution, block_indices),
        )

    def _unify_comprehension_core(
        self,
        nodes: Sequence[Union[ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp]],
        substitution: Substitution,
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
                if not self._unify_single_comprehension(comps, substitution, block_indices):
                    return False
            return finalize()
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_dict_comp(
        self, nodes: List[ast.DictComp], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        if not nodes:
            return False

        return self._unify_comprehension_core(
            nodes,
            substitution,
            block_indices,
            lambda: self._unify_nodes([n.key for n in nodes], substitution, block_indices)
            and self._unify_nodes([n.value for n in nodes], substitution, block_indices),
        )

    def _unify_with(
        self, nodes: List[ast.With], substitution: Substitution, block_indices: List[int]
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

        saved_alpha = dict(self.alpha_renamings)
        try:
            # Unify each item
            num_items = len(items_lists[0])
            for i in range(num_items):
                items_i = [lst[i] for lst in items_lists]
                # Unify context_expr
                if not self._unify_nodes(
                    [it.context_expr for it in items_i], substitution, block_indices
                ):
                    return False

                optional_vars_raw = [it.optional_vars for it in items_i]
                if all(ov is None for ov in optional_vars_raw):
                    pass  # nothing to do
                else:
                    # All must be AST; if any None while others not, fail
                    if not all_instances(optional_vars_raw, ast.expr):
                        return False
                    targets = optional_vars_raw
                    # Support simple Name or Tuple[List] of Names

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
                    for pos in range(arities[0]):
                        canonical = names_per_block[0][pos]
                        for idx, block_idx in enumerate(block_indices):
                            actual = names_per_block[idx][pos]
                            self.alpha_renamings[(block_idx, actual)] = canonical

            # Unify bodies
            if not self._unify_lists([n.body for n in nodes], substitution, block_indices):
                return False

            # type_comment (if present) must match
            comments = [getattr(n, "type_comment", None) for n in nodes]
            if not all(c == comments[0] for c in comments):
                return False

            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_except_handler(
        self, nodes: List[ast.ExceptHandler], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify ExceptHandler nodes, treating the 'name' as a bound identifier.
        """
        # Unify exception types (may be None)
        types_list: List[Optional[ast.expr]] = [n.type for n in nodes]
        if all(t is None for t in types_list):
            pass
        else:
            if not all_instances(types_list, ast.expr):
                return False
            if not self._unify_nodes(types_list, substitution, block_indices):
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
                if all_instances(names, str):
                    names_str = names
                    canonical = names_str[0]
                    for idx, block_idx in enumerate(block_indices):
                        actual = names_str[idx]
                        self.alpha_renamings[(block_idx, actual)] = canonical

            # Unify body
            if not self._unify_lists([n.body for n in nodes], substitution, block_indices):
                return False
            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_named_expr(
        self, nodes: List[ast.NamedExpr], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify walrus (NamedExpr) treating the target as a binding.
        Only simple Name targets are recognized for alpha-renaming.
        """
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
            if not self._unify_nodes([n.value for n in nodes], substitution, block_indices):
                return False
            # Do not require exact match for target identifiers (treated as bindings)
            return True
        finally:
            self.alpha_renamings = saved_alpha

    def _unify_ann_assign(
        self, nodes: List[ast.AnnAssign], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """Unify annotated assignments by target and value, ignoring the annotation."""
        if not all(isinstance(node, ast.AnnAssign) for node in nodes):
            return False
        if len({node.simple for node in nodes}) != 1:
            return False
        values = [node.value for node in nodes]
        if any(value is None for value in values):
            return all(value is None for value in values) and self._unify_nodes(
                [node.target for node in nodes], substitution, block_indices
            )
        if not all_instances(values, ast.expr):
            return False
        return self._unify_nodes(values, substitution, block_indices) and self._unify_nodes(
            [node.target for node in nodes], substitution, block_indices
        )

    def _unify_single_comprehension(
        self, comps: List[ast.comprehension], substitution: Substitution, block_indices: List[int]
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

        targets = [c.target for c in comps]

        # Simple Name targets
        if all_instances(targets, ast.Name):
            names = [t.id for t in targets]
            canonical = names[0]
            # Establish alpha-renaming for the duration of the entire comprehension
            for idx, block_idx in enumerate(block_indices):
                key = (block_idx, names[idx])
                self.alpha_renamings[key] = canonical

            # Unify iterator and ifs under alpha-renaming
            if not self._unify_nodes([c.iter for c in comps], substitution, block_indices):
                return False
            if not self._unify_lists([c.ifs for c in comps], substitution, block_indices):
                return False
            return True

        # Tuple targets with simple names
        if all_instances(targets, ast.Tuple):
            # All tuples must be flat and same length with Name elts
            lengths = [len(t.elts) for t in targets]
            if len(set(lengths)) != 1:
                return False
            name_rows: List[List[ast.Name]] = []
            for t in targets:
                if not all_instances(t.elts, ast.Name):
                    return False
                name_rows.append(t.elts)

            tuple_names: List[List[str]] = []  # per position names
            for pos in range(lengths[0]):
                tuple_names.append([row[pos].id for row in name_rows])

            # Establish alpha-renaming per position to the first block's names
            for pos, names_at_pos in enumerate(tuple_names):
                canonical = names_at_pos[0]
                for idx, block_idx in enumerate(block_indices):
                    key = (block_idx, names_at_pos[idx])
                    self.alpha_renamings[key] = canonical

            # Unify iterator and ifs
            if not self._unify_nodes([c.iter for c in comps], substitution, block_indices):
                return False
            if not self._unify_lists([c.ifs for c in comps], substitution, block_indices):
                return False
            return True

        # Fallback: complex targets - unify structurally without alpha-renaming
        return (
            self._unify_nodes([c.target for c in comps], substitution, block_indices)
            and self._unify_nodes([c.iter for c in comps], substitution, block_indices)
            and self._unify_lists([c.ifs for c in comps], substitution, block_indices)
        )

    def _unify_loop_components(
        self,
        nodes: Sequence[ast.For],
        substitution: Substitution,
        block_indices: Sequence[int],
    ) -> bool:
        return (
            self._unify_nodes([n.iter for n in nodes], substitution, block_indices)
            and self._unify_lists([n.body for n in nodes], substitution, block_indices)
            and self._unify_lists([n.orelse for n in nodes], substitution, block_indices)
        )

    def _unify_for_loop(
        self, nodes: List[ast.For], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify For loops, treating loop variables as bound (alpha-equivalent).

        Args:
            nodes: List of For nodes
            substitution: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # For loops have: target, iter, body, orelse
        # The 'target' is a bound variable - it can differ (i vs j) and that's OK

        targets = [n.target for n in nodes]

        if not all_instances(targets, ast.Name):
            if all_instances(targets, ast.Tuple):
                # Delegate to tuple unpacking handler
                return self._unify_for_loop_with_tuple_targets(nodes, substitution, block_indices)

            # Complex targets (nested structures, etc.) - fall back to default unification
            return (
                self._unify_nodes(targets, substitution, block_indices)
                and self._unify_nodes([n.iter for n in nodes], substitution, block_indices)
                and self._unify_lists([n.body for n in nodes], substitution, block_indices)
                and self._unify_lists([n.orelse for n in nodes], substitution, block_indices)
            )

        loop_var_names = [t.id for t in targets]

        if len(set(loop_var_names)) == 1:
            # Same loop variable name - just unify normally
            return self._unify_loop_components(nodes, substitution, block_indices)

        # Different loop variable names (i vs j) - establish alpha-equivalence
        canonical_var = loop_var_names[0]

        # Establish alpha-renaming mappings for all blocks
        old_mappings: Dict[Tuple[int, str], str] = {}
        for idx, block_idx in enumerate(block_indices):
            var_name = loop_var_names[idx]
            key = (block_idx, var_name)
            self._assign_alpha_mapping(key, canonical_var, old_mappings)

        try:
            # Unify the iterator
            return self._unify_loop_components(nodes, substitution, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for idx, block_idx in enumerate(block_indices):
                var_name = loop_var_names[idx]
                key = (block_idx, var_name)
                self._restore_alpha_mapping(key, old_mappings)

    def _unify_for_loop_with_tuple_targets(
        self, nodes: List[ast.For], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify For loops with tuple unpacking targets (e.g., for key, value in items).

        Handles flat tuple unpacking with alpha-renaming:
        - for key, value in pairs
        - for k, v in pairs

        Args:
            nodes: List of For nodes with Tuple targets
            substitution: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        targets = [n.target for n in nodes]

        if not all_instances(targets, ast.Tuple):
            return False
        tuple_lengths = [len(t.elts) for t in targets]
        if len(set(tuple_lengths)) != 1:
            return False
        name_rows: List[List[ast.Name]] = []
        for target in targets:
            if not all_instances(target.elts, ast.Name):
                return False
            name_rows.append(target.elts)

        # var_names[position_idx] = [name_in_block0, name_in_block1, ...]
        var_names = [[row[pos].id for row in name_rows] for pos in range(tuple_lengths[0])]

        # If so, no alpha-renaming needed
        all_same = all(len(set(names)) == 1 for names in var_names)

        if all_same:
            # All tuple unpacking uses same variable names - just unify normally
            return self._unify_loop_components(nodes, substitution, block_indices)

        # Different variable names - establish alpha-equivalence for each position
        canonical_vars = [names[0] for names in var_names]

        # Establish alpha-renaming mappings for all positions and blocks
        old_mappings: Dict[Tuple[int, str], str] = {}
        for pos_idx, canonical_var in enumerate(canonical_vars):
            for idx, block_idx in enumerate(block_indices):
                var_name = var_names[pos_idx][idx]
                key = (block_idx, var_name)
                self._assign_alpha_mapping(key, canonical_var, old_mappings)

        try:
            # Unify the iterator
            return self._unify_loop_components(nodes, substitution, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for pos_idx, canonical_var in enumerate(canonical_vars):
                for idx, block_idx in enumerate(block_indices):
                    var_name = var_names[pos_idx][idx]
                    key = (block_idx, var_name)
                    self._restore_alpha_mapping(key, old_mappings)

    def _unify_lambda(
        self, nodes: List[ast.Lambda], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify lambda expressions with alpha-renaming support.

        Lambda parameters are bindings, similar to for loop variables.
        If they differ (like 'lambda x: ...' vs 'lambda y: ...'), they're
        alpha-equivalent, not parameterizable.

        Args:
            nodes: List of Lambda nodes
            substitution: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        # For now, only handle simple case: same number of regular positional args
        param_counts = [len(n.args.args) for n in nodes]
        if len(set(param_counts)) > 1:
            # Different number of parameters - can't unify
            return False

        for node in nodes:
            if node.args.posonlyargs or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
                # Complex lambda parameters - not currently supported
                # See docs/KNOWN_LIMITATIONS.md for details
                return False

        # Defaults are evaluated where the lambda stands, before its parameters
        # exist, so they unify outside the parameters' renaming. Left out, a
        # block's own default was replaced by the template's, and only the
        # instantiation check noticed.
        defaults = [n.args.defaults for n in nodes]
        if any(len(block_defaults) != len(defaults[0]) for block_defaults in defaults):
            return False
        if not self._unify_lists(defaults, substitution, block_indices):
            return False

        num_params = param_counts[0]
        if num_params == 0:
            return self._unify_thunks(nodes, substitution, block_indices)

        canonical_params = [nodes[0].args.args[i].arg for i in range(num_params)]

        # Save old alpha-renaming mappings (in case of nested lambdas)
        old_mappings: Dict[Tuple[int, str], str] = {}
        try:
            for param_idx in range(num_params):
                canonical_param = canonical_params[param_idx]
                for idx, node in enumerate(nodes):
                    block_idx = block_indices[idx]
                    actual_param = node.args.args[param_idx].arg
                    key = (block_idx, actual_param)
                    self._assign_alpha_mapping(key, canonical_param, old_mappings)

            # Unify lambda bodies with alpha-renaming in effect
            return self._unify_nodes([n.body for n in nodes], substitution, block_indices)

        finally:
            # Restore old mappings or remove new ones
            for param_idx in range(num_params):
                for idx, node in enumerate(nodes):
                    block_idx = block_indices[idx]
                    actual_param = node.args.args[param_idx].arg
                    key = (block_idx, actual_param)
                    self._restore_alpha_mapping(key, old_mappings)

    def _unify_thunks(
        self, nodes: List[ast.Lambda], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """Unify lambdas that take nothing; one the blocks differ in as a whole is passed through.

        Where the blocks differ in a lambda's whole body, the body becomes a
        parameter and the helper makes a lambda of its own around it,
        ``lambda: __param_0()``. Handed to a call, that is a function made
        by the helper and named for it, where the block handed on one it made
        itself; a chain of helpers wraps it once more at every step. So when
        each lambda is an argument of a call, is made at most once per run of
        its block, and is the only lambda of its text there, the lambda
        itself is the parameter instead: the call site makes it, in the
        function that made it before, and the helper passes it on as it
        came (see ``semantic_safety.defer_impure_parameters``).

        Bodies that differ only in a free name (``lambda: a`` against
        ``lambda: b``) are left as they were: unifying them records that the
        names correspond, and the call sites would then pass the other
        block's spelling for a name the helper no longer reads.
        """
        bodies = [node.body for node in nodes]
        # A body an earlier parameter already stands for keeps that parameter.
        if not all(
            self._passed_on_once(node, index)
            and substitution.get_param_for_expr(index, node.body) is None
            for node, index in zip(nodes, block_indices)
        ):
            return self._unify_nodes(bodies, substitution, block_indices)
        counter, known = self.param_counter, set(substitution.param_expressions)
        correspondences = dict(self.alpha_renamings)
        if not self._unify_nodes(bodies, substitution, block_indices):
            return False
        fresh = [name for name in substitution.param_expressions if name not in known]
        if (
            len(fresh) != 1
            or fresh[0] in substitution.function_params
            or self.alpha_renamings != correspondences
            or sorted(
                (index, id(expression))
                for index, expression in substitution.param_expressions[fresh[0]]
            )
            != sorted((index, id(body)) for index, body in zip(block_indices, bodies))
        ):
            return True
        substitution.remove_parameter(fresh[0])
        self.param_counter = counter
        return self._try_parameterize(nodes, substitution, block_indices)

    def _passed_on_once(self, node: ast.Lambda, block_index: int) -> bool:
        """Whether a lambda is an argument of a call, made at most once per run of its block.

        It must also be the only lambda of its text in the block: a parameter
        stands for every occurrence of its text, and two lambdas the block
        made separately must not become one object.
        """
        if self.current_blocks is None or block_index >= len(self.current_blocks):
            return False
        block = self.current_blocks[block_index]
        parents = {
            id(child): parent
            for statement in block
            for parent in ast.walk(statement)
            for child in ast.iter_child_nodes(parent)
        }
        holder = parents.get(id(node))
        if isinstance(holder, ast.keyword):
            holder = parents.get(id(holder))
        if not isinstance(holder, ast.Call) or holder.func is node:
            return False
        ancestor = parents.get(id(node))
        while ancestor is not None:
            if isinstance(ancestor, _REPEATING):
                return False
            ancestor = parents.get(id(ancestor))
        text = structural_text(node)
        return (
            sum(
                1
                for statement in block
                for other in ast.walk(statement)
                if isinstance(other, ast.Lambda) and structural_text(other) == text
            )
            == 1
        )

    def _unify_joined_str(
        self, nodes: List[ast.JoinedStr], substitution: Substitution, block_indices: List[int]
    ) -> bool:
        """
        Unify f-strings (JoinedStr), never parameterizing Constant children.

        F-strings have strict structure requirements:
        - values list can only contain Constant or FormattedValue nodes
        - Constant nodes are string literals and must NEVER be parameterized
        - FormattedValue nodes contain expressions that CAN be unified/parameterized

        Args:
            nodes: List of JoinedStr nodes
            substitution: Current substitution
            block_indices: Block indices

        Returns:
            True if unification succeeded
        """
        values_lists = [n.values for n in nodes]
        if not all(len(v) == len(values_lists[0]) for v in values_lists):
            # Different number of components - can't unify
            return False

        # Unify each component
        for i in range(len(values_lists[0])):
            components = [values[i] for values in values_lists]

            component_types = [type(c) for c in components]
            if len(set(component_types)) != 1:
                # Different types at this position - can't unify
                return False

            if all_instances(components, ast.Constant):
                # String literal parts are compared, never parameterized
                values = [c.value for c in components]
                if not all(v == values[0] for v in values):
                    # Different string literals - can't unify f-strings with different text
                    return False

            elif all_instances(components, ast.FormattedValue):
                value_exprs = [c.value for c in components]
                if not self._unify_nodes(value_exprs, substitution, block_indices):
                    return False

                # Also check conversion and format_spec if present
                conversions = [c.conversion for c in components]
                if not all(conv == conversions[0] for conv in conversions):
                    return False

                # format_spec can be None or another JoinedStr
                format_specs = [c.format_spec for c in components]
                if format_specs[0] is not None:
                    if not all_instances(format_specs, ast.JoinedStr):
                        return False
                    if not self._unify_joined_str(format_specs, substitution, block_indices):
                        return False

            else:
                # Unexpected component type
                return False

        return True

    def _unify_lists(
        self,
        lists: Sequence[Sequence[Any]],
        substitution: Substitution,
        block_indices: Sequence[int],
    ) -> bool:
        """
        Unify lists of values.

        Args:
            lists: List of lists to unify
            substitution: Current substitution
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

            elem_types = set(type(e) for e in elements)
            if len(elem_types) > 1:
                return False

            first_elem = elements[0]

            if isinstance(first_elem, ast.AST):
                # AST nodes - unify recursively
                if not self._unify_nodes(elements, substitution, block_indices):
                    return False
            elif isinstance(first_elem, list):
                # Nested lists
                if not self._unify_lists(elements, substitution, block_indices):
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

    def _reset_unification_state(
        self,
        blocks: Sequence[Sequence[ast.AST]],
        translation_keywords: TranslationKeywords = DEFAULT_TRANSLATION_KEYWORDS,
        typing_forms: Optional[Sequence[TypingForms]] = None,
    ) -> None:
        self.param_counter = 0
        self._pattern_depth = 0
        self._pattern_parameters_allowed = False
        self.current_blocks = blocks
        forms = typing_forms if typing_forms is not None else [TYPING_FORMS_BY_NAME] * len(blocks)
        # What each block's statements pin, looked up by node id.
        self._read_in_place = [
            {
                node: pin
                for statement in block
                for node, pin in statically_read(
                    statement, translation_keywords, block_forms
                ).items()
            }
            for block, block_forms in zip(blocks, forms, strict=True)
        ]
        # A helper extracted on an earlier pass already binds names such as
        # ``__param_0``; a fresh parameter must not alias any identifier the
        # blocks mention, or the substituted body becomes ambiguous.
        self._reserved_parameter_names = set()
        for block in blocks:
            for statement in block:
                self._reserved_parameter_names |= mentioned_names(statement)

    # A construct that binds names is unified by a method of its own, chosen by
    # the node's type; each takes the nodes, the substitution and the block indices.
    _BINDING_CONSTRUCT_UNIFIERS: Dict[Type[ast.AST], Callable[..., bool]] = {
        ast.For: _unify_for_loop,
        ast.Lambda: _unify_lambda,
        ast.JoinedStr: _unify_joined_str,
        ast.ListComp: _unify_elt_comprehension,
        ast.SetComp: _unify_elt_comprehension,
        ast.GeneratorExp: _unify_elt_comprehension,
        ast.DictComp: _unify_dict_comp,
        ast.AnnAssign: _unify_ann_assign,
        ast.With: _unify_with,
        ast.ExceptHandler: _unify_except_handler,
        ast.NamedExpr: _unify_named_expr,
    }
