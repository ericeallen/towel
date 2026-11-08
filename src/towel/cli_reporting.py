# Copyright 2026 Eric Allen
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

"""Human review output and the versioned result schema, independent of CLI dispatch."""

from __future__ import annotations

from difflib import unified_diff
import json
from pathlib import Path
from typing import TYPE_CHECKING, List, Literal, Sequence, TypedDict

from towel.source_files import python_sources
from towel.source_text import read_source

if TYPE_CHECKING:
    from towel.unification.models import ParameterKind


class ChangeRecord(TypedDict):
    """One rewritten call site as the sidecar and the inventory record it."""

    file: str
    line: int
    before: str
    after: str


class CallRecord(TypedDict):
    """A call of a generated helper as found in the refactored tree."""

    file: str
    line: int
    bound: bool
    statement: str
    arguments: List[str]


class BindingRecord(TypedDict):
    """The argument expression one call site passes for a parameter."""

    file: str
    line: int
    expression: str


class ParameterRecord(TypedDict):
    """A helper parameter: its evaluation kind, rename key, and what each site passes."""

    name: str
    kind: ParameterKind
    rename_key: str
    bindings: List[BindingRecord]


HelperScopeKind = Literal["module", "class", "function"]
"""Where a helper was placed; the inventory's ``scope`` is this, then ``:<name>`` unless module.

A class's name is its dotted qualname within the module.
"""


class HelperRecord(TypedDict):
    """Everything a naming assistant is given about one generated helper."""

    file: str
    line: int
    name: str
    scope: str
    rename_key: str
    renameable: bool
    parameters: List[ParameterRecord]
    source: str
    calls: List[CallRecord]
    changes: List[ChangeRecord]


class MappingFormat(TypedDict):
    """How rename keys are spelled, for the assistant that writes the rename file."""

    helper: str
    parameter: str
    notes: str


class HelperInventory(TypedDict):
    """The JSON document ``rename-helpers --list --json`` prints."""

    target: str
    mapping_format: MappingFormat
    helpers: List[HelperRecord]


class CommandResult(TypedDict):
    """Version 1 of the complete-preview and dry result contract.

    A null publication state means the operation failed without confirming
    whether its final transaction committed. A time-limit refusal is always
    before publication and therefore reports false, as a preview always does.
    """

    schema_version: Literal[1]
    command: Literal["preview", "dry"]
    status: Literal["complete", "incomplete", "error"]
    input: str
    output: str | None
    published: bool | None
    applied: int
    termination: str
    diff: str
    helpers: HelperInventory | None
    error: str | None


def source_diff(source: Path, output: Path, excluded: Sequence[str]) -> str:
    """The complete final source diff, without inventory or progress text."""
    source, output = source.resolve(), output.resolve()
    output_base = output if output.is_dir() else output.parent
    lines: list[str] = []
    for after in python_sources(output, excluded=excluded):
        relative = after.relative_to(output_base)
        before = source / relative if source.is_dir() else source
        old = read_source(before) if before.exists() else ""
        new = read_source(after)
        if old == new:
            continue
        for line in unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"before/{relative}",
            tofile=f"after/{relative}",
        ):
            lines.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(lines)


def render_preview(
    applied: int,
    termination: str,
    has_declines: bool,
    diff: str,
    inventory: HelperInventory | None,
) -> str:
    """Present one completed result without repeating the same change in several forms."""
    lines = [f"Would apply {applied} refactoring(s).", f"Termination: {termination}"]
    if not applied:
        lines.append(
            "No applicable refactorings found." if has_declines else "No duplicates found!"
        )
    if diff:
        lines.append(diff.rstrip("\n"))
    if inventory is not None:
        lines.extend(("", "Helper inventory:", json.dumps(inventory, indent=2)))
    lines.append("Input files were not changed.")
    return "\n".join(lines) + "\n"
