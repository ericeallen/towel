"""Node-independent keys and values for caching analyses across re-parses.

Every fixed-point iteration re-parses the files it changed, so caches keyed by
node identity miss for the whole file even though most of its blocks are
untouched. A block's *structure*, the dump of its statements without
positions, survives re-parsing and the line shifts an edit elsewhere in the
file causes. Values that reference nodes are stored as positions within the
block and resolved against whichever block matches the structure later; the
extractor matches expressions by their dump, so a node at the same position
in a structurally identical block behaves identically.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .substitution import Substitution

# A path names a node inside a block: the statement index, then (field, index)
# steps; ``index`` is -1 for a single-valued field.
Step = Tuple[str, int]
Path = Tuple[int, Tuple[Step, ...]]


def structural_id(nodes: Sequence[ast.AST]) -> str:
    """A digest of the nodes' structure: everything but positions."""
    digest = hashlib.sha256()
    for node in nodes:
        digest.update(ast.dump(node, include_attributes=False).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def path_map(block: Sequence[ast.AST]) -> Dict[ast.AST, Path]:
    """The position of every node in ``block``."""
    paths: Dict[ast.AST, Path] = {}
    for statement_index, statement in enumerate(block):
        stack: List[Tuple[ast.AST, Tuple[Step, ...]]] = [(statement, ())]
        while stack:
            node, steps = stack.pop()
            paths[node] = (statement_index, steps)
            for field, value in ast.iter_fields(node):
                if isinstance(value, ast.AST):
                    stack.append((value, steps + ((field, -1),)))
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, ast.AST):
                            stack.append((item, steps + ((field, index),)))
    return paths


def resolve_path(block: Sequence[ast.AST], path: Path) -> ast.AST:
    """The node at ``path`` in ``block``."""
    statement_index, steps = path
    node: ast.AST = block[statement_index]
    for field, index in steps:
        value = getattr(node, field)
        node = value if index < 0 else value[index]
    return node


@dataclass(frozen=True)
class StoredSubstitution:
    """A substitution with every node reference replaced by its position."""

    mappings: Tuple[Tuple[Tuple[int, str], str], ...]
    param_expressions: Tuple[Tuple[str, Tuple[Tuple[int, Path], ...]], ...]
    function_params: Tuple[Tuple[str, Tuple[str, ...]], ...]
    hygienic_renames: Tuple[Tuple[Tuple[str, str], ...], ...]
    params_used_as_callee: Tuple[str, ...]
    inlined_parameters: Tuple[str, ...]
    promoted_literal_args: Tuple[Tuple[str, Tuple[Tuple[int, Path], ...]], ...]
    renames: Tuple[Tuple[Tuple[str, str], ...], ...]


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
    )


def load_substitution(
    stored: StoredSubstitution, blocks: Sequence[Sequence[ast.AST]]
) -> Tuple[Substitution, List[Dict[str, str]]]:
    """A fresh substitution over ``blocks``, plus the hygienic renames to hand back."""
    substitution = Substitution(
        mappings=dict(stored.mappings),
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
    return substitution, [dict(mapping) for mapping in stored.renames]
