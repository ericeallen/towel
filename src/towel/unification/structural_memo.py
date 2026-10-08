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

"""Node-independent keys and values for caching analyses across re-parses.

Every fixed-point iteration re-parses the files it changed, so caches keyed by
node identity miss for the whole file even though most of its blocks are
untouched. A block's *structure*, the dump of its statements without
positions, survives re-parsing and the line shifts an edit elsewhere in the
file causes. Values that reference nodes are stored as positions within the
block and resolved against whichever block matches the structure later; the
extractor matches constant occurrences by their field/index paths and other
expressions by their dump, so structurally identical blocks reuse the same
selection without retaining the original nodes.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, cast

from weakref import WeakKeyDictionary

from .statement_facts import memoized_per_node
from ..canonical_ast import canonical_dump
from .substitution import LiteralPool, Substitution
from .ast_paths import (
    Path as Path,
    Step as Step,
    path_map as path_map,
    resolve_path as resolve_path,
)

__all__ = [
    "Path",
    "Step",
    "path_map",
    "resolve_path",
    "structural_id",
    "StoredLiteralPool",
    "StoredSubstitution",
    "store_substitution",
    "load_substitution",
]


def _node_digest(node: ast.AST) -> bytes:
    return hashlib.sha256(canonical_dump(node).encode("utf-8")).digest()


_NODE_DIGESTS: "WeakKeyDictionary[ast.AST, bytes]" = WeakKeyDictionary()


def structural_id(nodes: Sequence[ast.AST]) -> str:
    """A digest of the nodes' structure: everything but positions.

    Each node's digest is memoized for its lifetime, since a statement is
    part of every block that spans it and each block is identified many
    times. The digests are of fixed width, so their concatenation names the
    sequence unambiguously. The value is a per-run key, not a stable format.
    """
    digest = hashlib.sha256()
    for node in nodes:
        digest.update(memoized_per_node(_NODE_DIGESTS, node, _node_digest))
    return digest.hexdigest()


@dataclass(frozen=True)
class StoredLiteralPool:
    parameter: str
    keys: Tuple[str, ...]
    rows: Tuple[Tuple[Path, ...], ...]
    selectors: Tuple[Tuple[Tuple[Path, int], ...], ...]


@dataclass(frozen=True)
class StoredSubstitution:
    """A substitution with every node reference replaced by its position."""

    mappings: Tuple[Tuple[Tuple[int, str], str], ...]
    occurrence_mappings: Tuple[Tuple[Tuple[int, Path], str], ...]
    param_expressions: Tuple[Tuple[str, Tuple[Tuple[int, Path], ...]], ...]
    function_params: Tuple[Tuple[str, Tuple[str, ...]], ...]
    hygienic_renames: Tuple[Tuple[Tuple[str, str], ...], ...]
    params_used_as_callee: Tuple[str, ...]
    inlined_parameters: Tuple[str, ...]
    promoted_literal_args: Tuple[Tuple[str, Tuple[Tuple[int, Path], ...]], ...]
    renames: Tuple[Tuple[Tuple[str, str], ...], ...]
    literal_pool: Optional[StoredLiteralPool] = None


def store_substitution(
    substitution: Substitution,
    blocks: Sequence[Sequence[ast.AST]],
    renames: Sequence[Dict[str, str]],
) -> StoredSubstitution:
    """Freeze a unification result so it no longer refers to ``blocks``' nodes."""
    maps = [path_map(block) for block in blocks]

    def located(block_index: int, node: ast.AST) -> Path:
        return maps[block_index][node]

    return StoredSubstitution(
        mappings=tuple(sorted(substitution.mappings.items())),
        occurrence_mappings=tuple(sorted(substitution.occurrence_mappings.items())),
        param_expressions=tuple(
            (name, tuple((index, located(index, node)) for index, node in expressions))
            for name, expressions in substitution.param_expressions.items()
        ),
        function_params=tuple(
            (name, tuple(params)) for name, params in substitution.function_params.items()
        ),
        hygienic_renames=tuple(
            tuple(sorted(mapping.items())) for mapping in substitution.hygienic_renames
        ),
        params_used_as_callee=tuple(sorted(substitution.params_used_as_callee)),
        inlined_parameters=tuple(sorted(substitution.inlined_parameters)),
        promoted_literal_args=tuple(
            (name, tuple((index, located(index, node)) for index, node in by_block.items()))
            for name, by_block in substitution.promoted_literal_args.items()
        ),
        renames=tuple(tuple(sorted(mapping.items())) for mapping in renames),
        literal_pool=(
            None
            if substitution.literal_pool is None
            else StoredLiteralPool(
                substitution.literal_pool.parameter,
                substitution.literal_pool.keys,
                tuple(
                    tuple(located(index, node) for node in row)
                    for index, row in enumerate(substitution.literal_pool.rows)
                ),
                substitution.literal_pool.selectors,
            )
        ),
    )


def load_substitution(
    stored: StoredSubstitution, blocks: Sequence[Sequence[ast.AST]]
) -> Tuple[Substitution, List[Dict[str, str]]]:
    """A fresh substitution over ``blocks``, plus the hygienic renames to hand back."""
    substitution = Substitution(
        mappings=dict(stored.mappings),
        occurrence_mappings=dict(stored.occurrence_mappings),
        param_expressions={
            name: [(index, resolve_path(blocks[index], path)) for index, path in expressions]
            for name, expressions in stored.param_expressions
        },
        function_params={name: list(params) for name, params in stored.function_params},
        hygienic_renames=[dict(mapping) for mapping in stored.hygienic_renames],
        params_used_as_callee=set(stored.params_used_as_callee),
        inlined_parameters=set(stored.inlined_parameters),
        promoted_literal_args={
            name: {index: resolve_path(blocks[index], path) for index, path in by_block}
            for name, by_block in stored.promoted_literal_args
        },
    )
    if stored.literal_pool is not None:
        rows = tuple(
            tuple(resolve_path(blocks[index], path) for path in row)
            for index, row in enumerate(stored.literal_pool.rows)
        )
        assert all(isinstance(node, ast.expr) for row in rows for node in row)
        substitution.literal_pool = LiteralPool(
            stored.literal_pool.parameter,
            stored.literal_pool.keys,
            tuple(tuple(cast(ast.expr, node) for node in row) for row in rows),
            stored.literal_pool.selectors,
        )
    substitution.bind_source_paths(blocks)
    return substitution, [dict(mapping) for mapping in stored.renames]
