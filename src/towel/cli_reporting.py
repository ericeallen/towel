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

from dataclasses import dataclass
from difflib import unified_diff
import json
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    List,
    Literal,
    NotRequired,
    Optional,
    Sequence,
    TypedDict,
)

from towel.diagnostics import LOG
from towel.reason_sentences import Explanation, explain, reason_code
from towel.source_files import python_sources
from towel.source_text import decode_source

if TYPE_CHECKING:
    from towel.unification.fixed_point import RunReport
    from towel.unification.models import LeftAlone, ParameterKind

Outcome = Literal["no_python_files", "no_repeated_code", "all_left_alone", "applied"]
"""How a run ended, for its user: nothing to read, nothing alike, all left alone, or changed."""

LARGEST_SHOWN = 5
"""How many of the largest places left alone a run lists without ``--explain``."""


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


TypesStatus = Literal["verified", "unverified", "off"]


def mode_name(strict: bool) -> Literal["relaxed", "strict"]:
    """The result's name for the mode a run was asked for."""
    return "strict" if strict else "relaxed"


class SkippedRecord(TypedDict):
    """A file the run could not read, and why."""

    path: str
    reason: str


class AnalysedRecord(TypedDict):
    """What the run's first analysis read: its files, their functions, the files it skipped."""

    files: int
    functions: int
    skipped: List[SkippedRecord]


class CommandResult(TypedDict):
    """Version 1 of the complete-preview and dry result contract.

    A null publication state means the operation failed without confirming
    whether its final transaction committed. A time-limit refusal is always
    before publication and therefore reports false, as a preview always does.

    On completed runs, ``types`` is ``verified`` when a checker ran and
    verified the accepted changes, ``unverified`` when no checker was
    available, and ``off`` for explicit ``--no-types``. Failed or interrupted
    runs omit it: ``status`` and ``error`` describe their outcome. It is also
    omitted when a checker was available but no readable source needed checking.
    A missing configured checker in relaxed mode gives ``unverified`` and bare
    helpers; with no configured checker, existing annotations may be copied.
    """

    config_path: str | None
    schema_version: Literal[1]
    types: NotRequired[TypesStatus]
    command: Literal["preview", "dry"]
    mode: Literal["relaxed", "strict"]
    status: Literal["complete", "incomplete", "error"]
    input: str
    output: str | None
    published: bool | None
    applied: int
    termination: str
    diff: str
    helpers: HelperInventory | None
    error: str | None
    outcome: Outcome | None
    analysed: AnalysedRecord | None
    left_alone: int


def source_diff(source: Path, output: Path, excluded: Sequence[str]) -> str:
    """The complete final source diff, without inventory or progress text.

    Files are compared as bytes first, and only a changed file is decoded: a
    file the run could not read is one it left as it was. A changed file
    that does not decode is left out of the diff with a warning naming it.
    """
    source, output = source.resolve(), output.resolve()
    output_base = output if output.is_dir() else output.parent
    lines: list[str] = []
    for after in python_sources(output, excluded=excluded):
        relative = after.relative_to(output_base)
        before = source / relative if source.is_dir() else source
        old_bytes = before.read_bytes() if before.exists() else b""
        new_bytes = after.read_bytes()
        if old_bytes == new_bytes:
            continue
        try:
            old, new = decode_source(old_bytes), decode_source(new_bytes)
        except (UnicodeError, SyntaxError) as error:
            LOG.warning("Not showing the change to %s: it does not decode (%s)", before, error)
            continue
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
    headline: Sequence[str],
    diff: str,
    inventory: HelperInventory | None,
    after: Sequence[str] = (),
) -> str:
    """Present one completed result without repeating the same change in several forms.

    ``headline`` follows the count (the census and the outcome), and ``after``
    (why code was left alone) comes before the closing line.
    """
    lines = [f"Would apply {applied} refactoring(s).", *headline]
    if diff:
        lines.append(diff.rstrip("\n"))
    if inventory is not None and inventory["helpers"]:
        lines.extend(("", "Helper inventory:", json.dumps(inventory, indent=2)))
    lines.extend(after)
    lines.append("Input files were not changed.")
    return "\n".join(lines) + "\n"


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def run_outcome(report: "RunReport", applied: int) -> Outcome:
    """Which of the four ways a run can end this one ended."""
    if applied:
        return "applied"
    if report.files_analysed == 0:
        return "no_python_files"
    if report.declined_pairs or report.declined_proposals:
        return "all_left_alone"
    return "no_repeated_code"


def census_line(report: "RunReport") -> str:
    """What the run read: ``Analysed 3 Python files (12 functions).``"""
    line = (
        f"Analysed {_plural(report.files_analysed, 'Python file')}"
        f" ({_plural(report.functions_analysed, 'function')})."
    )
    if report.skipped_files:
        line += f" Skipped {_plural(len(report.skipped_files), 'file')} it could not read."
    return line


def outcome_line(report: "RunReport", applied: int, target: str) -> Optional[str]:
    """The one sentence for a run that changed nothing; None for one that did."""
    outcome = run_outcome(report, applied)
    if outcome == "no_python_files":
        if report.skipped_files:
            return f"No Python file under {target} could be read."
        return f"No Python files found under {target}."
    if outcome == "no_repeated_code":
        return f"No repeated code found in {_plural(report.files_analysed, 'file')}."
    if outcome == "all_left_alone":
        places = len(report.left_alone) or sum(report.declined_pairs.values()) + sum(
            report.declined_proposals.values()
        )
        return f"Found {_plural(places, 'place')} with similar code and extracted none."
    return None


def stopped_at_limit(termination: str, limit: int) -> Optional[str]:
    """The sentence for a run that stopped at ``--max-refactorings``; None for a finished one."""
    if termination != "iteration_cap":
        return None
    return f"Stopped at the limit of {_plural(limit, 'refactoring')} (--max-refactorings)."


@dataclass(frozen=True)
class _ReasonTally:
    explanation: Explanation
    codes: Dict[str, None]
    subjects: Dict[str, None]
    count: int


def shows_pairs(applied: int, every: bool) -> bool:
    """Whether a report lists the declined pairs of the run's last analysis.

    After a run applied something, its last analysis read the rewritten code,
    and its declined pairs include the calls the run itself wrote; they are
    listed only on request (``--explain``). Proposals not applied are always
    listed.
    """
    return every or not applied


def left_alone_count(report: "RunReport", applied: int) -> int:
    """How many places the report lists as left alone by default (``shows_pairs``)."""
    if shows_pairs(applied, False):
        return len(report.left_alone)
    pair_reasons = set(report.declined_pairs)
    return sum(1 for site in report.left_alone if site.reason not in pair_reasons)


UNRESOLVED_NAMES_REASON = "not verifiable: its file holds a name the type checker cannot type"
"""The proposals a typed run could not verify because their file holds a name it cannot type."""


def reason_lines(
    report: "RunReport", *, pairs: bool = True, first_unverifiable: Optional[str] = None
) -> List[str]:
    """Each reason once, with how often it applied and its sentence, most frequent first.

    Pairs and proposals counted under reasons with the same sentence are one
    line. Each line ends with the reason's code in brackets, the name the
    documentation and the developer trace use. ``pairs`` leaves out the
    declined pairs when false (``shows_pairs``). The line for proposals the
    type checker could not verify names ``first_unverifiable``, the first
    unresolved name's diagnostic, when the run has one.
    """
    counts: Dict[str, int] = {}
    for reason, count in (report.declined_pairs if pairs else {}).items():
        counts[reason] = counts.get(reason, 0) + count
    for proposal_reason, count in report.declined_proposals.items():
        counts[str(proposal_reason)] = counts.get(str(proposal_reason), 0) + count
    tallies: Dict[str, _ReasonTally] = {}
    for counted, count in counts.items():
        code = reason_code(counted)
        explanation = explain(code)
        previous = tallies.get(explanation.sentence)
        subject = counted[len(code) + 1 : -1] if counted != code else None
        tallies[explanation.sentence] = _ReasonTally(
            explanation,
            {**(previous.codes if previous else {}), code: None},
            {
                **(previous.subjects if previous else {}),
                **({subject: None} if subject else {}),
            },
            (previous.count if previous else 0) + count,
        )
    if not tallies:
        return []
    lines = ["", "Why code was left alone (most frequent first; --explain locates each place):"]
    width = len(str(max(tally.count for tally in tallies.values())))
    for tally in sorted(tallies.values(), key=lambda t: (-t.count, next(iter(t.codes)))):
        subjects = f" (Here: {', '.join(tally.subjects)}.)" if tally.subjects else ""
        if UNRESOLVED_NAMES_REASON in tally.codes:
            subjects += (
                f" First problem: {first_unverifiable}"
                if first_unverifiable
                else " A name used by the code has no type the checker can establish."
            )
        lines.append(
            f"  {tally.count:>{width}}  {tally.explanation.sentence}{subjects}"
            f" [{', '.join(tally.codes)}]"
        )
        if tally.explanation.remedy:
            lines.append(f"  {'':>{width}}  What you can do: {tally.explanation.remedy}")
    return lines


def _span(path: str, first: int, last: int, relative: Callable[[str], str]) -> str:
    return f"{relative(path)}:{first}-{last}"


def left_alone_lines(
    report: "RunReport", relative: Callable[[str], str], *, every: bool, pairs: bool = True
) -> List[str]:
    """The places left alone, largest first, one line each: the five largest, or ``every`` one.

    ``pairs`` leaves out the declined pairs when false (``shows_pairs``).
    """
    pair_reasons = set(report.declined_pairs)
    located: List["LeftAlone"] = sorted(
        (
            site
            for site in report.left_alone
            if site.blocks and (pairs or site.reason not in pair_reasons)
        ),
        key=lambda site: (
            -site.lines,
            tuple((block.path, block.first_line) for block in site.blocks),
        ),
    )
    if not located:
        return []
    shown = located if every else located[:LARGEST_SHOWN]
    heading = (
        "Places left alone, largest first:"
        if every or len(located) <= LARGEST_SHOWN
        else f"The {LARGEST_SHOWN} largest of {len(located)} places left alone"
        " (--explain lists them all):"
    )
    lines = ["", heading]
    for site in shown:
        spans = [_span(b.path, b.first_line, b.last_line, relative) for b in site.blocks]
        where = spans[0] if len(spans) == 1 else ", ".join(spans[:-1]) + " and " + spans[-1]
        lines.append(f"  {where}: {explain(site.reason).sentence}")
    return lines


def analysed_record(report: "RunReport", relative: Callable[[str], str]) -> AnalysedRecord:
    """The census as the JSON result reports it."""
    return {
        "files": report.files_analysed,
        "functions": report.functions_analysed,
        "skipped": [
            {"path": relative(skipped.path), "reason": skipped.reason}
            for skipped in report.skipped_files
        ],
    }
