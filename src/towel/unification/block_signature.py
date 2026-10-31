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

"""Conservative block signature utilities used for cheap pre-filtering.

Signatures retain the structural and count gates used by quick_filter. The
exact gates (statement count, try/with presence, and the statement-type
sequence) form the candidate-index key, ``signature_bucket_key``; name/call
count tolerances stay in quick_filter. Indexing therefore changes comparison
cost, not the set of accepted candidates.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .statement_facts import statement_facts

DEFAULT_SIMILARITY_THRESHOLD = 0.6
"""Structural similarity a clustered occurrence must reach to join a helper."""

IDENT_COUNT_TOLERANCE = 2


@dataclass(frozen=True)
class BlockSignature:
    """Structural fingerprint of a code block for fast similarity pre-filtering.

    Contains conservative metrics (statement count, control flow presence, name/call counts)
    used to quickly reject obviously dissimilar blocks before expensive unification.
    """

    stmt_count: int
    stmt_seq: Tuple[str, ...]
    has_with: bool
    has_try: bool
    name_load_count: int
    name_store_count: int
    call_count: int


@dataclass(frozen=True)
class _SignaturePrefixes:
    statement_types: Tuple[str, ...]
    with_counts: Tuple[int, ...]
    try_counts: Tuple[int, ...]
    name_load_counts: Tuple[int, ...]
    name_store_counts: Tuple[int, ...]
    call_counts: Tuple[int, ...]

    @classmethod
    def of(cls, body: Sequence[ast.stmt]) -> "_SignaturePrefixes":
        columns: List[List[int]] = [[0] for _ in range(5)]
        for statement in body:
            facts = statement_facts(statement)
            for column, value in zip(
                columns,
                (
                    int(facts.has_with),
                    int(facts.has_try),
                    facts.name_load_count,
                    facts.name_store_count,
                    facts.call_count,
                ),
            ):
                column.append(column[-1] + value)
        return cls(
            tuple(type(statement).__name__ for statement in body), *(tuple(c) for c in columns)
        )

    def signature(self, start: int, stop: int) -> BlockSignature:
        return BlockSignature(
            stmt_count=stop - start,
            stmt_seq=self.statement_types[start:stop],
            has_with=self.with_counts[stop] != self.with_counts[start],
            has_try=self.try_counts[stop] != self.try_counts[start],
            name_load_count=self.name_load_counts[stop] - self.name_load_counts[start],
            name_store_count=self.name_store_counts[stop] - self.name_store_counts[start],
            call_count=self.call_counts[stop] - self.call_counts[start],
        )


class BlockSignatureIndex:
    """Read-only prefix facts for contiguous suites in one immutable source function.

    The index lives only for one enumeration. Nested definitions are separate
    functions and are not indexed. Statement lists and the exact type sequence
    remain owned by the source tree and each returned signature respectively.
    """

    def __init__(self, function: ast.AST) -> None:
        positions: Dict[ast.stmt, Tuple[_SignaturePrefixes, int]] = {}
        pending = [function]
        while pending:
            node = pending.pop()
            if node is not function and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            for _, value in ast.iter_fields(node):
                if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                    body = [statement for statement in value if isinstance(statement, ast.stmt)]
                    prefixes = _SignaturePrefixes.of(body)
                    positions.update(
                        (statement, (prefixes, index)) for index, statement in enumerate(body)
                    )
            pending.extend(ast.iter_child_nodes(node))
        self._positions = positions

    def signature(self, block: Sequence[ast.stmt]) -> Optional[BlockSignature]:
        """Summarize an original contiguous source-suite slice, in its original order.

        The caller supplies an actual slice of the indexed source. Endpoint
        checks locate that interval and allow an unindexed suite to fall back;
        they do not validate arbitrary permutations of its middle statements.
        """
        if not block:
            return None
        first = self._positions.get(block[0])
        last = self._positions.get(block[-1])
        if first is None or last is None or first[0] is not last[0]:
            return None
        prefixes, start = first
        if last[1] != start + len(block) - 1:
            return None
        return prefixes.signature(start, start + len(block))


def extract_block_signature(
    block: Sequence[ast.stmt], *, index: Optional[BlockSignatureIndex] = None
) -> BlockSignature:
    """The structural summary two blocks must share before unification is attempted.

    Nested definitions and lambdas are not looked into: their bodies are
    other scopes, and the unifier treats them by structure. Each statement's
    share is memoized (``statement_facts``). An optional source-suite index
    answers the numeric facts from prefix sums instead of repeatedly folding
    overlapping intervals. With an index, ``block`` must be an original,
    contiguous source-suite slice in its original order. Exact statement-type
    tuples are still retained, so their allocation remains proportional to
    the total interval lengths.
    """
    if index is not None:
        indexed = index.signature(block)
        if indexed is not None:
            return indexed
    has_with = False
    has_try = False
    name_load_count = 0
    name_store_count = 0
    call_count = 0
    for stmt in block:
        facts = statement_facts(stmt)
        has_with = has_with or facts.has_with
        has_try = has_try or facts.has_try
        name_load_count += facts.name_load_count
        name_store_count += facts.name_store_count
        call_count += facts.call_count

    return BlockSignature(
        stmt_count=len(block),
        stmt_seq=tuple(type(s).__name__ for s in block),
        has_with=has_with,
        has_try=has_try,
        name_load_count=name_load_count,
        name_store_count=name_store_count,
        call_count=call_count,
    )


BlockBucketKey = Tuple[int, bool, bool, Tuple[str, ...]]


def signature_bucket_key(signature: BlockSignature) -> BlockBucketKey:
    """Return exact gates that every unifiable pair must share.

    The statement-type sequence is part of the key: the unifier walks the
    two blocks in lockstep and cannot unify statements of different kinds
    (a statement is not an expression it could turn into a parameter), so
    two blocks whose sequences differ never unify, and partitioning on the
    sequence changes what is compared, not what is accepted. ``quick_filter``
    requires the same. Name and call count tolerances are not equivalence
    relations and stay out of the key.
    """
    return signature.stmt_count, signature.has_with, signature.has_try, signature.stmt_seq


def quick_filter(sig1: BlockSignature, sig2: BlockSignature) -> bool:
    """Return True if the pair should be considered for unification.

    Extremely simple structural checks matching the original conservative
    behavior prior to aggressive experimentation.
    """
    if sig1.stmt_count != sig2.stmt_count:
        return False
    if sig1.has_with != sig2.has_with or sig1.has_try != sig2.has_try:
        return False
    if abs(sig1.name_load_count - sig2.name_load_count) > IDENT_COUNT_TOLERANCE:
        return False
    if abs(sig1.name_store_count - sig2.name_store_count) > IDENT_COUNT_TOLERANCE:
        return False
    if abs(sig1.call_count - sig2.call_count) > IDENT_COUNT_TOLERANCE:
        return False
    if sig1.stmt_seq and sig2.stmt_seq and sig1.stmt_seq != sig2.stmt_seq:
        return False
    return True
