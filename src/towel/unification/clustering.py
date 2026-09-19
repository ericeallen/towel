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
same helper. A clustered site joins only when it passes the same guards,
for a method helper is a method of the same classes with the same receiver
kind, and, when the helper reads shared names bare, resolves every such
name at module scope too (a same-spelled local of the candidate's function
or of an enclosing one keeps it out); a site whose scope cannot see the
helper is skipped. The
per-candidate pipeline is memoized on the template, the candidate, and the
helper, and its constant-time filters run before the semantic guards.

The scan of a file is itself memoized on the file's content and the
template: with N similar blocks in one file, N^2 pairs each produce the same
template, and each used to scan all N sites again. A scan lists every
admissible site; the pair's own blocks and overlaps are filtered out on the
way out, so every pair sees exactly what its own scan would have found.
Cached call nodes and replacements are shared, never copied: nothing
downstream mutates a replacement's node without deep-copying it first.
"""

from __future__ import annotations

import ast

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterator, List, Optional, Sequence, Set, Tuple
from .assignment_analyzer import has_reassignments_without_bindings
from .block_analysis import align_return_variables
from .block_signature import DEFAULT_SIMILARITY_THRESHOLD, extract_block_signature, quick_filter
from .extractor import HygienicExtractor, UnsupportedExtraction
from .instantiation import instantiation_mismatch
from .models import FunctionArtifact, FunctionNode, Replacement
from .orphan_detector import orphaned_variables
from .scope_analyzer import ScopeAnalyzer
from .statement_facts import statement_shape
from .semantic_safety import (
    available_argument_names,
    module_resolved_names,
    defer_impure_parameters,
    has_impure_eager_parameters,
    moves_scope_declaration,
    nested_bindings_escape,
    nested_scopes_cross_block_boundary,
    block_requires_original_frame,
    frame_read_outside_block,
    unbinds_external_name,
)
from .thunk_inlining import inline_leading_thunks
from .visitors import body_without_docstring

from .builtins import CALL_ARGUMENT_BUILTINS
from .engine_state import ClusteredSite, ClusterKey, ClusterScanKey, TemplateKey
from .insertion import InsertionPoints
from .placement import HelperPlacement
from .block_analysis import BlockAnalysis
from .function_index import FunctionIndex
from .models import BlockBindingSnapshot, ClusterContext, HelperTemplate, encloses


@dataclass(frozen=True)
class _ClusterCandidate:
    """A candidate occurrence tested for whether it can share a helper."""

    file_path: str
    function: FunctionNode
    analyzer: ScopeAnalyzer
    nodes: List[ast.stmt]
    snapshot: BlockBindingSnapshot
    # Names the block binds for the first time that are read after it.
    return_variables: FrozenSet[str]


def _lines_of(line_range: Tuple[int, int]) -> range:
    """The lines an inclusive range covers."""
    return range(line_range[0], line_range[1] + 1)


class Clustering(InsertionPoints, HelperPlacement, BlockAnalysis):
    """Clustering methods of the engine; see the module docstring."""

    def _cluster_candidate_call(
        self, template: "HelperTemplate", candidate: "_ClusterCandidate"
    ) -> Optional[ast.stmt]:
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
        )
        if not subst2:
            return None
        # The helper reads the template's module names bare; a candidate whose
        # same-spelled name is a local of its function or of an enclosing one
        # would read something else through the helper.
        if template.module_names:
            resolved = module_resolved_names(
                candidate.function, candidate.analyzer, template.module_names
            )
            if resolved != template.module_names:
                return None
        if (
            self._rebound_external_names(candidate.function, candidate.nodes, candidate.analyzer)
            - template.module_names
        ):
            return None
        available = (
            template.available_names,
            available_argument_names(candidate.function, candidate.nodes, candidate.analyzer),
        )
        defer_impure_parameters(subst2, pair.block1_nodes, available)
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
            return_variables=list(template.return_variables),
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
        if has_impure_eager_parameters(subst2, available):
            return None
        # The helper returns a fixed tuple; the candidate may read after its
        # block only names that map into it, and its call assigns them all.
        aligned = align_return_variables(
            set(template.return_variables),
            set(candidate.return_variables),
            set(template.bound_in_block),
            set(candidate.snapshot.bound_in_block),
            cluster_renames,
        )
        if aligned is None or aligned[0] != list(template.return_variables):
            return None
        returned = aligned[1]
        # A name the call rebinds is not orphaned by moving the block.
        indices = self._get_block_indices(candidate.function, candidate.nodes)
        if indices is None:
            return None
        body = body_without_docstring(candidate.function.body)
        if orphaned_variables(body, indices) - set(returned):
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
                return_variables=returned,
                hygienic_renames=cluster_renames,
            )
        except UnsupportedExtraction:
            return None
        # Validate candidate call-site does not reference undefined names
        used2 = self._used_names(call_node2)
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
                returns_variables=bool(template.return_variables),
            )
            is not None
        ):
            return None
        allowed = set(candidate.snapshot.bound_before_block) | set(
            candidate.analyzer.free_variables(candidate.nodes)
        )
        if any(
            name != template.func_def.name
            and name not in allowed
            and name not in CALL_ARGUMENT_BUILTINS
            for name in used2
        ):
            return None
        return call_node2

    def _add_clustered_replacements(
        self,
        template: "HelperTemplate",
        dce_node: Optional[FunctionNode],
        functions: FunctionIndex,
        replacements: List[Replacement],
        cluster_contexts: Dict[int, ClusterContext],
    ) -> None:
        """Append same-file occurrences that can share the extracted helper.

        Takes the file's admissible sites for the template (scanned once per
        template, see ``_clustered_sites``) and appends, in scan order, each
        that overlaps neither the pair's own blocks nor a site already taken,
        recording the method context needed to decide, later, whether that
        call can dispatch through a receiver. Mutates ``replacements`` and
        ``cluster_contexts`` in place.
        """
        pair = template.pair
        covered: Set[int] = set(_lines_of(pair.block1_range))
        covered.update(_lines_of(pair.block2_range))
        for site in self._clustered_sites(template, dce_node, functions):
            lines = _lines_of(site.replacement.line_range)
            if not covered.isdisjoint(lines):
                continue
            cluster_contexts[len(replacements)] = site.context
            replacements.append(site.replacement)
            covered.update(lines)

    def _clustered_sites(
        self,
        template: "HelperTemplate",
        dce_node: Optional[FunctionNode],
        functions: FunctionIndex,
    ) -> Tuple[ClusteredSite, ...]:
        """Every block of the pair's file that can call the template's helper, in file order.

        Memoized on the file's content and the template, since every pair
        that produces the template asks the same question of the same file.
        A file whose digest is unknown is scanned afresh.
        """
        pair = template.pair
        module_digest = self._module_digest(pair.function1_node)
        if module_digest is None:
            return tuple(self._scan_clustered_sites(template, dce_node, functions))
        key = ClusterScanKey(
            self._template_key(template),
            pair.file_path,
            module_digest,
            (
                None
                if dce_node is None
                else (dce_node.lineno, dce_node.col_offset, self._sid([dce_node]))
            ),
            self.min_lines,
        )
        cached = self._cluster_scan_cache.get(key)
        if cached is None:
            cached = self._cluster_scan_cache.put(
                key, tuple(self._scan_clustered_sites(template, dce_node, functions))
            )
        return cached

    def _scan_clustered_sites(
        self,
        template: "HelperTemplate",
        dce_node: Optional[FunctionNode],
        functions: FunctionIndex,
    ) -> Iterator[ClusteredSite]:
        """Scan every function in the pair's file for blocks that unify with the template.

        Yields every admissible block, overlapping ones included: which of
        them a pair takes depends on the pair's own blocks, so that choice is
        made by the caller.
        """
        pair = template.pair
        template_signature = extract_block_signature(pair.block1_nodes)
        template_key = self._template_key(template)

        for entry in functions.in_file(pair.file_path):
            fn = entry.node
            # A helper inserted into the pair's deepest common enclosing
            # function is visible only there and in its nested functions;
            # a block elsewhere in the file cannot call it (prompt_toolkit).
            if dce_node is not None and not encloses(dce_node, fn):
                continue
            # Where the candidate sits decides, once the helper's home is
            # known, whether it can share a method call.
            candidate_class = self._method_class(fn, entry.class_name, entry.scope_analyzer)
            candidate_info = self._get_method_context(fn, candidate_class)
            context = ClusterContext(candidate_class, candidate_info)
            fn_id = self._sid([fn])
            module_digest = self._module_digest(fn)
            for cand_range, cand_nodes, cand_sig in self._signed_blocks(fn):
                # The size gate and signature filter are constant-time and
                # reject most blocks; the semantic guards each walk the
                # candidate's function, so they run only on survivors. Every
                # check is independent, so the order changes cost, not outcome.
                if cand_range[1] - cand_range[0] + 1 < self.min_lines:
                    continue
                if not quick_filter(template_signature, cand_sig):
                    continue
                cand_id = self._sid(cand_nodes)
                candidate = self._admissible_candidate(entry, fn_id, cand_nodes, cand_id)
                if candidate is None:
                    continue
                key = ClusterKey(template_key, cand_id, fn_id, module_digest)
                call_node = self._clustered_call(key, template, candidate)
                if call_node is None:
                    continue
                yield ClusteredSite(
                    Replacement(
                        line_range=cand_range,
                        node=call_node,
                        file_path=entry.file_path,
                        class_name=entry.class_name,
                        method_kind=None,
                        implicit_param=None,
                    ),
                    context,
                )

    def _admissible_candidate(
        self,
        entry: FunctionArtifact,
        fn_id: str,
        cand_nodes: List[ast.stmt],
        cand_id: str,
    ) -> Optional["_ClusterCandidate"]:
        """The candidate block as a cluster candidate, or None when a semantic guard declines it.

        The same guards the pair stages apply to a block: frame sensitivity,
        escaping nested bindings, rebinding of snapshotted names, scopes
        crossing the boundary, moved scope declarations, reassignment of a
        name the block did not bind, and unbinding of a name bound before it.
        """
        fn, fpath, analyzer = entry.node, entry.file_path, entry.scope_analyzer
        for frame_guard in (block_requires_original_frame, frame_read_outside_block):
            if self._block_rejected(
                frame_guard, cand_nodes, fn, analyzer, function_id=fn_id, block_id=cand_id
            ):
                return None
        for guard in (
            nested_bindings_escape,
            nested_scopes_cross_block_boundary,
            moves_scope_declaration,
        ):
            if self._block_rejected(guard, cand_nodes, fn, function_id=fn_id, block_id=cand_id):
                return None
        reassignments = self._get_assignment_reuse(fn)
        if self._per_block(
            "reassignments",
            fn,
            cand_nodes,
            lambda: has_reassignments_without_bindings(fn, cand_nodes, reassignments),
            function_id=fn_id,
            block_id=cand_id,
        )[0]:
            return None
        cand_range = self._block_line_span(cand_nodes)
        if cand_range is None:
            return None
        snapshot = self._build_block_binding_snapshot(
            fn, cand_nodes, cand_range, reassignments, function_id=fn_id, block_id=cand_id
        )
        if self._per_block(
            "unbinds",
            fn,
            cand_nodes,
            lambda: unbinds_external_name(fn, cand_nodes, snapshot.bound_before_block),
            function_id=fn_id,
            block_id=cand_id,
        ):
            return None
        return _ClusterCandidate(
            file_path=fpath,
            function=fn,
            analyzer=analyzer,
            nodes=cand_nodes,
            snapshot=snapshot,
            return_variables=frozenset(
                self._find_return_variables(fn, cand_range, snapshot.initially_bound, cand_nodes)
            ),
        )

    def _template_key(self, template: "HelperTemplate") -> TemplateKey:
        """Everything the template carries that a clustered call depends on, hashable."""
        return TemplateKey(
            self._sid(template.pair.block1_nodes),
            frozenset(template.free_vars),
            frozenset(template.enclosing_names),
            template.is_value_producing,
            tuple(sorted(template.globals_to_declare)),
            tuple(sorted(template.nonlocals_to_declare)),
            template.func_def.name,
            template.func_def_dump,
            tuple(sorted(template.param_order.items())),
            template.preamble_length,
            template.available_names,
            template.return_variables,
            template.module_names,
            template.bound_in_block,
        )

    def _clustered_call(
        self, key: ClusterKey, template: "HelperTemplate", candidate: "_ClusterCandidate"
    ) -> Optional[ast.stmt]:
        """The candidate's call, memoized under ``key``; every hit is the same node, never mutated."""
        if key in self._cluster_cache:
            return self._cluster_cache[key]
        return self._cluster_cache.put(key, self._cluster_candidate_call(template, candidate))

    def _are_structurally_similar(
        self,
        block1: Sequence[ast.AST],
        block2: Sequence[ast.AST],
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

        # Each statement's node count and type histogram are memoized per
        # statement: a block is compared against every candidate it pairs with.
        for stmt1, stmt2 in zip(block1, block2):
            shape1 = statement_shape(stmt1)
            shape2 = statement_shape(stmt2)

            # Must have similar number of nodes
            total = max(shape1.node_count, shape2.node_count)
            if abs(shape1.node_count - shape2.node_count) / total > 0.3:
                return False

            # Count common types
            common = sum((shape1.type_counts & shape2.type_counts).values())

            total_nodes += total
            matching_nodes += common

        if total_nodes == 0:
            return False

        similarity = matching_nodes / total_nodes
        return similarity >= threshold
