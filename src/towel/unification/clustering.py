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

"""Adding further call sites to an accepted helper.

Once a pair is accepted and its helper template fixed, the rest of the
file is scanned for blocks that unify with the template and can call the
same helper. A clustered site joins only when it passes the same guards
and, for a method helper, is a method of the same classes with the same
receiver kind; a site whose scope cannot see the helper is skipped. The
per-candidate pipeline is memoized on the template, the candidate, and the
helper, and its constant-time filters run before the semantic guards.
"""

from __future__ import annotations

import ast
import copy

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple, cast
from .assignment_analyzer import has_reassignments_without_bindings
from .block_signature import DEFAULT_SIMILARITY_THRESHOLD, extract_block_signature, quick_filter
from .extractor import HygienicExtractor, UnsupportedExtraction
from .instantiation import instantiation_mismatch
from .models import FunctionArtifact, FunctionNode, Replacement
from .orphan_detector import has_orphaned_variables
from .overlap import line_ranges_intersect
from .scope_analyzer import ScopeAnalyzer
from .semantic_safety import (
    defer_impure_parameters,
    has_impure_eager_parameters,
    moves_scope_declaration,
    nested_bindings_escape,
    nested_scopes_cross_block_boundary,
    requires_original_frame,
    snapshots_rebound_external_names,
    unbinds_external_name,
)
from .thunk_inlining import inline_leading_thunks
from .visitors import body_without_docstring

from .engine_state import EngineState
from .models import BlockBindingSnapshot, HelperTemplate, encloses


@dataclass(frozen=True)
class _ClusterCandidate:
    """A candidate occurrence tested for whether it can share a helper."""

    file_path: str
    function: FunctionNode
    analyzer: Optional[ScopeAnalyzer]
    nodes: List[ast.AST]
    snapshot: BlockBindingSnapshot


class Clustering(EngineState):
    """Clustering methods of the engine; see the module docstring."""

    def _cluster_candidate_call(
        self, template: "HelperTemplate", candidate: "_ClusterCandidate"
    ) -> Optional[ast.AST]:
        """The call replacing a clustered occurrence, or None when it cannot share the helper.

        Everything here is a function of the template and candidate blocks'
        structure, the candidate's function and module, and the pair's helper,
        so the caller memoizes it on exactly those.
        """
        pair = template.pair
        cluster_renames: List[Dict[str, str]] = [{}, {}]
        subst2 = self._unify_memoized(
            [pair.block1_nodes, candidate.nodes],
            cluster_renames,
            (pair.file_path, candidate.file_path),
        )
        if not subst2:
            return None
        defer_impure_parameters(subst2, pair.block1_nodes)
        # Unifying another occurrence may require a different,
        # more general helper. Its parameter numbers alone do
        # not identify the meanings of the existing helper's
        # arguments. Only reuse the helper when extraction from
        # this substitution produces the same body/signature.
        candidate_helper, candidate_order = HygienicExtractor().extract_function(
            template_block=pair.block1_nodes,
            substitution=subst2,
            free_variables=template.free_vars,
            enclosing_names=template.enclosing_names,
            is_value_producing=template.is_value_producing,
            global_decls=template.globals_to_declare or None,
            nonlocal_decls=template.nonlocals_to_declare or None,
            function_name=template.func_def.name,
        )
        inline_leading_thunks(candidate_helper, subst2, candidate_order)
        if (
            candidate_order != template.param_order
            or ast.dump(candidate_helper) != template.func_def_dump
        ):
            return None
        if has_impure_eager_parameters(subst2):
            return None
        # Orphan check for candidate within its function body
        indices = self._get_block_indices(candidate.function, candidate.nodes)
        if indices is None:
            return None
        # Skip docstring in body
        body = body_without_docstring(candidate.function.body)
        has_orph, _orph = has_orphaned_variables(cast(List[ast.AST], body), indices)
        if has_orph:
            return None
        # Generate a call node for the candidate
        try:
            call_node2 = self.extractor.generate_call(
                function_name=template.func_def.name,
                block_idx=1,
                substitution=subst2,
                param_order=template.param_order,
                free_variables=template.free_vars,
                is_value_producing=template.is_value_producing,
                return_variables=[],
                hygienic_renames=cluster_renames,
            )
        except UnsupportedExtraction:
            return None
        # Validate candidate call-site does not reference undefined names
        used2 = self._get_used_names(call_node2)
        if any(n.startswith("__param_") for n in used2):
            # Skip brittle candidate that leaked placeholders
            return None
        if (
            instantiation_mismatch(
                template.func_def,
                call_node2,
                candidate.nodes,
                cluster_renames[0],
                cluster_renames[1],
                preamble_length=template.preamble_length,
                returns_variables=False,
            )
            is not None
        ):
            return None
        bound_before_cand: Set[str] = set(candidate.snapshot.bound_before_block)
        free_vars_cand: Set[str] = set()
        if candidate.analyzer is not None:
            free_vars_cand = set(candidate.analyzer.get_free_variables(candidate.nodes))
        allowed_cand = bound_before_cand | free_vars_cand
        builtin_whitelist = {
            "len",
            "sum",
            "min",
            "max",
            "any",
            "all",
            "map",
            "filter",
            "sorted",
            "list",
            "dict",
            "set",
            "range",
            "int",
            "float",
            "str",
            "bool",
            "enumerate",
            "zip",
        }
        invalid2 = {
            name
            for name in used2
            if name != template.func_def.name
            and name not in allowed_cand
            and name not in builtin_whitelist
        }
        if invalid2:
            return None
        # Append replacement
        return call_node2

    def _add_clustered_replacements(
        self,
        template: "HelperTemplate",
        dce_node: Optional[FunctionNode],
        all_functions: Sequence[FunctionArtifact],
        replacements: List[Replacement],
        cluster_contexts: Dict[int, Tuple[Optional[str], Optional[str], Optional[str], bool]],
    ) -> None:
        """Append same-file occurrences that can share the extracted helper.

        Scans every function in the pair's file for additional blocks that unify
        with the template and reproduce its helper, appending a call for each and
        recording the method context needed to decide, later, whether that call
        can dispatch through a receiver. Mutates ``replacements`` and
        ``cluster_contexts`` in place.
        """
        pair = template.pair

        # Build a set of already covered ranges to avoid duplicates
        covered = {
            (pair.file_path, pair.block1_range),
            (pair.file_path2 or pair.file_path, pair.block2_range),
        }
        # Template signature from block1
        tmpl_sig = extract_block_signature(pair.block1_nodes)
        func_def_dump = ast.dump(template.func_def)

        # Gather candidates from same file functions
        for entry in all_functions:
            fpath = entry.file_path
            fn = entry.node
            analyzerX = entry.scope_analyzer
            clsX = entry.class_name
            if fpath != pair.file_path:
                continue
            # A helper inserted into the pair's deepest common enclosing
            # function is visible only there and in its nested functions;
            # a block elsewhere in the file cannot call it (prompt_toolkit).
            if dce_node is not None and not encloses(dce_node, fn):
                continue
            # Where the candidate sits decides, once the helper's home is
            # known, whether it can share a method call (see below).
            candidate_class = self._method_class(fn, clsX, analyzerX)
            candidate_info = self._get_method_context(fn, candidate_class)
            # Skip the original two functions
            if fn.name in (pair.function1_name, pair.function2_name):
                # Still scan, but avoid ranges we've already taken
                pass
            # Extract blocks and test quick filter against template
            for cand_range, cand_nodes, cand_sig in self._signed_blocks(fn):
                if any(
                    path == fpath and line_ranges_intersect(cand_range, taken)
                    for path, taken in covered
                ):
                    continue
                # The size gate and signature filter are constant-time and
                # reject most blocks; the semantic guards below each walk the
                # candidate's function, so they run only on survivors. Every
                # check is independent, so the order changes cost, not outcome.
                start_line, end_line = cand_range
                if (end_line - start_line + 1) < self.min_lines:
                    continue
                if not quick_filter(tmpl_sig, cand_sig):
                    continue
                if self._block_rejected(requires_original_frame, cand_nodes, path=fpath):
                    continue
                if self._block_rejected(nested_bindings_escape, cand_nodes, fn):
                    continue
                if self._block_rejected(
                    snapshots_rebound_external_names, cand_nodes, fn, analyzerX
                ):
                    continue
                if self._block_rejected(nested_scopes_cross_block_boundary, cand_nodes, fn):
                    continue
                if self._block_rejected(moves_scope_declaration, cand_nodes, fn):
                    continue
                reassignX = self._get_assignment_reuse(fn)
                if self._per_block(
                    "reassignments",
                    fn,
                    cand_nodes,
                    lambda: has_reassignments_without_bindings(fn, cand_nodes, reassignX),
                )[0]:
                    continue
                candidate_snapshot = self._build_block_binding_snapshot(
                    fn, cand_nodes, cand_range, reassignX
                )
                if self._per_block(
                    "unbinds",
                    fn,
                    cand_nodes,
                    lambda: unbinds_external_name(
                        fn, cand_nodes, candidate_snapshot.bound_before_block
                    ),
                ):
                    continue
                # Try to unify template block with candidate
                memo_key = (
                    self._sid(pair.block1_nodes),
                    self._sid(cand_nodes),
                    self._sid([fn]),
                    self._module_digest(fn),
                    frozenset(template.free_vars),
                    frozenset(template.enclosing_names),
                    template.is_value_producing,
                    tuple(sorted(template.globals_to_declare)),
                    tuple(sorted(template.nonlocals_to_declare)),
                    template.func_def.name,
                    func_def_dump,
                    tuple(sorted(template.param_order.items())),
                    template.preamble_length,
                )
                if memo_key in self._cluster_cache:
                    cached_call = self._cluster_cache[memo_key]
                    self._cluster_cache.move_to_end(memo_key)
                    if cached_call is None:
                        continue
                    call_node2 = copy.deepcopy(cached_call)
                else:
                    computed = self._cluster_candidate_call(
                        template,
                        _ClusterCandidate(
                            file_path=fpath,
                            function=fn,
                            analyzer=analyzerX,
                            nodes=cand_nodes,
                            snapshot=candidate_snapshot,
                        ),
                    )
                    self._bounded_put(
                        self._cluster_cache,
                        memo_key,
                        None if computed is None else copy.deepcopy(computed),
                    )
                    if computed is None:
                        continue
                    call_node2 = computed
                cluster_contexts[len(replacements)] = (
                    candidate_class,
                    candidate_info.kind,
                    candidate_info.implicit_param,
                    candidate_info.receiver_known,
                )
                replacements.append(
                    Replacement(
                        line_range=cand_range,
                        node=call_node2,
                        file_path=fpath,
                        class_name=clsX,
                        method_kind=None,
                        implicit_param=None,
                    )
                )
                covered.add((fpath, cand_range))

    def _are_structurally_similar(
        self,
        block1: List[ast.AST],
        block2: List[ast.AST],
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> bool:
        """
        Check if two blocks are structurally similar enough to attempt unification.

        This does a rough structural comparison to filter out obviously different blocks.

        Args:
            block1: First block
            block2: Second block
            threshold: Similarity threshold (0.0 to 1.0, default: 0.6)

        Returns:
            True if blocks are similar enough
        """
        if len(block1) != len(block2):
            return False

        total_nodes = 0
        matching_nodes = 0

        for stmt1, stmt2 in zip(block1, block2):
            # Compare AST structure
            nodes1 = list(ast.walk(stmt1))
            nodes2 = list(ast.walk(stmt2))

            # Must have similar number of nodes
            if abs(len(nodes1) - len(nodes2)) / max(len(nodes1), len(nodes2)) > 0.3:
                return False

            # Count matching node types
            types1 = [type(n).__name__ for n in nodes1]
            types2 = [type(n).__name__ for n in nodes2]

            # Count common types
            from collections import Counter

            counter1 = Counter(types1)
            counter2 = Counter(types2)

            common = sum((counter1 & counter2).values())
            total = max(len(types1), len(types2))

            total_nodes += total
            matching_nodes += common

        if total_nodes == 0:
            return False

        similarity = matching_nodes / total_nodes
        return similarity >= threshold
