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

"""The callees Towel recognizes as reading the source or position of the call that invokes them.

A callee that reads its caller's frame or source is reflection, which Towel
does not model (docs/KNOWN_LIMITATIONS.md). It recognizes a callee here only
when the callee is a popular tool whose purpose is to read its call site,
and the code that calls it is the kind Towel extracts. For 1.772 that is
inline-snapshot: snapshot tests are near-identical by construction, and
``snapshot()`` keys each snapshot by its call's position and reads the
literal there, so two tests merged into one helper raise ``UsageError``.

Each entry records the absolute name it resolves to, what it reads, and the
verification: the version whose source was read and where the read is.
``source_readers`` decides which names in a block denote one; this is the
list it consults. An entry is added only with such a citation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Literal, Mapping, Optional, Sequence, Set, Tuple

ReaderKind = Literal["call", "caller"]
"""What a callee reads.

``call``: the call that invokes it, its source text or its position.
``caller``: the same of the call of the function it is called from, so that
function reads its own call in turn."""


@dataclass(frozen=True)
class KnownSourceReader:
    """A callee read in its library's source and found to read the code or position of its call.

    ``origin`` is the absolute dotted name the callee resolves to; ``kind``
    is what it reads (``ReaderKind``); ``note`` is the verification.
    """

    origin: str
    kind: ReaderKind
    note: str


_INLINE_SNAPSHOT = "inline-snapshot 0.35.4"


def _known(origins: Sequence[str], kind: ReaderKind, note: str) -> Tuple[KnownSourceReader, ...]:
    return tuple(KnownSourceReader(origin, kind, note) for origin in origins)


KNOWN_SOURCE_READERS: Tuple[KnownSourceReader, ...] = (
    *_known(
        ["inline_snapshot.snapshot", "inline_snapshot._inline_snapshot.snapshot"],
        "call",
        f"{_INLINE_SNAPSHOT}, _inline_snapshot.py: create_snapshot takes the frame two up"
        " (the caller), keys the snapshot by _types.key_for(frame) = (id(f_code), f_lasti),"
        " and SnapshotReference reads the call's argument node through"
        " AdapterContext(frame).expr; a second value at one key raises UsageError.",
    ),
    *_known(
        ["inline_snapshot.external", "inline_snapshot._external._external.external"],
        "call",
        f"{_INLINE_SNAPSHOT}, _external/_external.py: create_snapshot(External, name), keyed"
        " and located as snapshot is.",
    ),
    *_known(
        ["inline_snapshot.snapshot_arg", "inline_snapshot._snapshot_arg.snapshot_arg"],
        "caller",
        f"{_INLINE_SNAPSHOT}, _snapshot_arg.py: SnapshotArgReference reads its own call through"
        " Source.executing(frame) and the call of the function holding it through"
        " _get_call_frame (frame.f_back), whose argument it rewrites.",
    ),
)
"""Of inline-snapshot's other public names, read in the same source: ``outsource`` returns its
data or an ``Outsourced`` value (_external/_outsource.py), ``Is`` compares by value (_is.py),
and ``get_snapshot_value`` unwraps one (_get_snapshot_value.py); none reads a frame.
``external_file`` reads only its caller's file (_external/_external_file.py), which a helper in
the same module shares; ``customize_repr``, ``register_format`` and the rest register values
and read no caller."""

_BY_ORIGIN: Mapping[str, KnownSourceReader] = {
    entry.origin: entry for entry in KNOWN_SOURCE_READERS
}


def prefixes(origin: str) -> Tuple[str, ...]:
    """``a.b().c`` and every shorter name it is an attribute or call result of, longest first."""
    found = []
    end = len(origin)
    while end > 0:
        found.append(origin[:end])
        end = max(origin.rfind(".", 0, end), origin.rfind("()", 0, end))
    return tuple(found)


def _kinds_at_or_below() -> Mapping[str, FrozenSet[ReaderKind]]:
    kinds: Dict[str, Set[ReaderKind]] = {}
    for entry in KNOWN_SOURCE_READERS:
        for prefix in prefixes(entry.origin):
            kinds.setdefault(prefix, set()).add(entry.kind)
    return {prefix: frozenset(found) for prefix, found in kinds.items()}


KINDS_AT_OR_BELOW: Mapping[str, FrozenSet[ReaderKind]] = _kinds_at_or_below()
"""For every listed origin and each name it is an attribute or call result of
(``inline_snapshot`` for ``inline_snapshot.snapshot``), what the entries at or below it read.
A name that is none of these, and none of their attributes, is no reader."""


def nearest_listed(origin: str) -> Optional[str]:
    """``origin`` as far as the list cares; None when no listed reader is above or below it.

    Below an entry (``inline_snapshot.snapshot.x``), it is that entry, since
    what a reader holds is taken to read as well; an entry, or a name above
    one (``inline_snapshot``), is itself. So the names an analysis keeps are
    finitely many, however long the attribute chains it follows
    (``node = node.next`` in a loop).
    """
    for prefix in prefixes(origin):
        if prefix in _BY_ORIGIN:
            return prefix
    return origin if origin in KINDS_AT_OR_BELOW else None


def known_source_reader(origin: str) -> Optional[KnownSourceReader]:
    """The entry ``origin`` names exactly, if any."""
    return _BY_ORIGIN.get(origin)
