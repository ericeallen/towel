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

Signatures retain the structural and count gates used by quick_filter. Exact
statement-count and try/with gates also provide a conservative candidate index;
name/call count tolerances and optional endpoint checks remain in quick_filter.
Indexing therefore changes comparison cost, not the set of accepted candidates.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Sequence, Tuple

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


def extract_block_signature(block: Sequence[ast.AST]) -> BlockSignature:
    """The structural summary two blocks must share before unification is attempted.

    Nested definitions and lambdas are not looked into: their bodies are
    other scopes, and the unifier treats them by structure. Each statement's
    share is memoized (``statement_facts``) and folded, so enumerating every
    contiguous sub-block of a body costs one walk of the body, not one per
    block.
    """
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
