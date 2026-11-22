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
from heapq import nsmallest
from itertools import repeat
import json
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    NotRequired,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypedDict,
)

from towel.diagnostics import LOG
from towel.relaxed_suitability import FindingJSON
from towel.reason_sentences import (
    Explanation,
    cross_module_condition,
    explain,
    reason_code,
    without_subject,
)
from towel.source_files import python_sources
from towel.source_text import decode_source
from towel.unification.pair_locations import (
    EXPLAIN_PLACES_PER_REASON as EXPLAIN_PLACES_PER_REASON,
    LARGEST_SHOWN as LARGEST_SHOWN,
    ReasonPlaces,
    location_rank,
    place_rank,
)
from towel.unification.models import RejectReason

if TYPE_CHECKING:
    from towel.unification.fixed_point import RunReport
    from towel.unification.models import AppliedChange
    from towel.unification.models import BlockLocation, LeftAlone, NotExamined, ParameterKind

Outcome = Literal[
    "no_python_files", "no_repeated_code", "not_fully_examined", "all_left_alone", "applied"
]
"""How a run ended, including whether unexamined code limits a negative result."""


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


class LimitedFunctionRecord(TypedDict):
    function: str
    line: int
    windows: int
    limit: int


class NotExaminedRecord(TypedDict):
    """Unsupported code in one file; counts do not establish that the code repeats."""

    path: str
    module_statements: int
    early_return_guards: int
    limited_functions: NotRequired[List[LimitedFunctionRecord]]


class AnalysedRecord(TypedDict):
    """What the run's first analysis read: its files, their functions, the files it skipped."""

    files: int
    functions: int
    skipped: List[SkippedRecord]
    not_examined: List[NotExaminedRecord]
    resource_guard: NotRequired[str]


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
    partial_diff: NotRequired[str]
    finally_confirmed: NotRequired[bool]
    relaxed_findings: NotRequired[List[FindingJSON]]


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
    """What the run established, without treating unexamined code as repetition-free."""
    if applied:
        return "applied"
    if report.files_analysed == 0:
        return "no_python_files"
    if report.declined_pairs or report.declined_proposals:
        return "all_left_alone"
    return "not_fully_examined" if report.not_examined else "no_repeated_code"


def _skipped_counts(report: "RunReport") -> tuple[int, int, int]:
    """Unavailable inputs, Git-ignored inputs and explicitly excluded inputs."""
    ignored = sum(entry.reason.startswith("Git ignores it") for entry in report.skipped_files)
    excluded = sum("excluded from analysis" in entry.reason for entry in report.skipped_files)
    return len(report.skipped_files) - ignored - excluded, ignored, excluded


_RESOURCE_LIMIT_NOTE = (
    "The re-export work limit was reached. Unresolved calls may be resources, "
    "so Towel leaves their blocks in place."
)


def census_line(report: "RunReport") -> str:
    """What the run read: ``Analysed 3 Python files (12 functions).``"""
    line = (
        f"Analysed {_plural(report.files_analysed, 'Python file')}"
        f" ({_plural(report.functions_analysed, 'function')})."
    )
    unreadable, ignored, excluded = _skipped_counts(report)
    if unreadable:
        line += f" Skipped {_plural(unreadable, 'file')} it could not read or parse."
    if ignored:
        line += (
            f" Skipped {_plural(ignored, 'file')} Git ignores (--include-ignored includes them)."
        )
    if excluded:
        line += f" Skipped {_plural(excluded, 'file')} in excluded directories."
    if report.resource_limit_reached:
        line += " " + _RESOURCE_LIMIT_NOTE
    return line


def outcome_line(report: "RunReport", applied: int, target: str) -> Optional[str]:
    """The one sentence for a run that changed nothing; None for one that did."""
    outcome = run_outcome(report, applied)
    if outcome == "no_python_files":
        if report.skipped_files:
            unreadable, ignored, excluded = _skipped_counts(report)
            if not unreadable:
                return f"No Python files selected for analysis under {target}."
            if ignored or excluded:
                return f"No selected Python file under {target} could be read."
            return f"No Python file under {target} could be read."
        return f"No Python files found under {target}."
    if outcome == "not_fully_examined":
        return (
            "No repeated code found in the code Towel examined in "
            f"{_plural(report.files_analysed, 'file')}."
        )
    if outcome == "no_repeated_code":
        return f"No repeated code found in {_plural(report.files_analysed, 'file')}."
    if outcome == "all_left_alone":
        places = report.left_alone_total or sum(report.declined_pairs.values()) + sum(
            report.declined_proposals.values()
        )
        return f"Found {_plural(places, 'place')} with similar code and extracted none."
    return None


def not_examined_lines(
    report: "RunReport", relative: Callable[[str], str], *, nothing_found: bool, explain: bool
) -> List[str]:
    """Qualify an empty search once. With --explain, locate every coverage limit."""
    count = len(report.not_examined)
    limited = sum(len(entry.limited_functions) for entry in report.not_examined)
    if limited:
        summary = (
            f"Towel limited its search for repeated blocks inside {_plural(limited, 'function')}"
        )
        if not explain:
            return [summary + ". Use --explain to see what was not examined."]
    else:
        if not count or not (nothing_found or explain):
            return []
        summary = (
            f"{_plural(count, 'file')} {'holds' if count == 1 else 'hold'} module-level code "
            "or early-return guards that Towel does not examine"
        )
        if not explain:
            return [summary + "; --explain lists them."]
    lines = [summary + "."]
    for entry in report.not_examined:
        path = relative(entry.path)
        if entry.module_statements:
            lines.append(
                f"Module-level code is not examined: {path} has "
                f"{_plural(entry.module_statements, 'executable statement')}. "
                "Move code you want Towel to compare into a function."
            )
        if entry.early_return_guards:
            lines.append(
                f"Early-return guards are not examined on their own: {path} has "
                f"{_plural(entry.early_return_guards, 'guard')}. "
                "Extract the shared condition into a function, keeping each return in its caller."
            )
        for function in entry.limited_functions:
            lines.append(
                f"{path}:{function.line}: {function.function} has {function.windows:,} similar "
                f"blocks of consecutive statements. Towel considered the first {function.limit:,} in source order "
                "when looking for repetition inside this function."
            )
    return lines


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
        return report.left_alone_total
    pair_reasons = set(report.declined_pairs)
    return sum(site.occurrences for site in report.left_alone if site.reason not in pair_reasons)


UNRESOLVED_NAMES_REASON = "not verifiable: its file holds a name the type checker cannot type"
"""The proposals a typed run could not verify because their file holds a name it cannot type."""


def reason_lines(
    report: "RunReport",
    *,
    pairs: bool = True,
    first_unverifiable: Optional[str] = None,
    locate: Callable[[str], str] | None = None,
) -> List[str]:
    """Each reason once, with how often it applied and its sentence, most frequent first.

    Pairs and proposals counted under reasons with the same sentence are one
    line. The bracketed codes are the names the documentation and developer
    trace use. When given a path formatter, locate the largest distinct blocks
    below each reason. ``pairs`` leaves out the
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
        kind = without_subject(counted)
        explanation = explain(kind)
        previous = tallies.get(explanation.sentence)
        subject = counted[len(kind) + 1 : -1] if counted != kind else None
        if locate and previous and previous.subjects:
            # One subject illustrates the reason even when each module pair names its own.
            subject = None
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
    heading = (
        "Why code was left alone, most frequent reason first (largest places shown):"
        if locate
        else "Why code was left alone (most frequent first; --explain locates each place):"
    )
    lines = ["", heading]
    proposals = _proposal_places(report.left_alone) if locate else {}
    omitted = False
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
        if locate:
            pair_group = report.explained_pairs.get(tally.explanation.sentence)
            proposal_group = proposals.get(tally.explanation.sentence)
            places, more = _explained_place_lines(pair_group, proposal_group, locate)
            if tally.explanation.remedy:
                lines[-1] += f" What you can do: {tally.explanation.remedy}"
            lines.extend(places)
            if more:
                omitted = True
                lines.append(
                    f"    ... and {_plural(more, 'more place')} left alone for this reason"
                )
        elif tally.explanation.remedy:
            lines.append(f"  {'':>{width}}  What you can do: {tally.explanation.remedy}")
    if omitted:
        lines.append("To see more, run Towel on the directory or file you care about.")
    return lines


def _span(path: str, first: int, last: int, relative: Callable[[str], str]) -> str:
    return f"{relative(path)}:{first}-{last}"


def _proposal_places(sites: Iterable[LeftAlone]) -> dict[str, ReasonPlaces]:
    groups: dict[str, ReasonPlaces] = {}
    for site in sites:
        sentence = explain(without_subject(site.reason)).sentence
        if sentence not in groups:
            groups[sentence] = ReasonPlaces()
        group = groups[sentence]
        blocks = set(site.blocks)
        for place in blocks:
            group.record_place(place)
        # These occurrences all instantiated the same proposed helper.
        for place in group.shown.keys() & blocks:
            group.shown[place].update((other, 1.0) for other in blocks if other != place)
    return groups


def _explained_place_lines(
    pairs: ReasonPlaces | None, proposals: ReasonPlaces | None, relative: Callable[[str], str]
) -> tuple[list[str], int]:
    groups = [group for group in (pairs, proposals) if group is not None]
    total = sum(len(group.seen) for group in groups)
    if pairs and proposals:
        total -= len(pairs.seen & proposals.seen)
    # A place outside a group's largest twenty cannot enter the combined top twenty.
    candidates = {place for group in groups for place in group.shown}
    shown = nsmallest(EXPLAIN_PLACES_PER_REASON, candidates, key=place_rank)
    lines = []
    for place in shown:
        peers: dict[BlockLocation, float] = {}
        for group in groups:
            for other, score in group.shown.get(place, {}).items():
                peers[other] = max(score, peers.get(other, 0.0))
        where = _span(place.path, place.first_line, place.last_line, relative)
        if peers:
            best = min(
                peers,
                key=lambda b: (
                    -peers[b],
                    abs(b.lines - place.lines),
                    b.path,
                    b.first_line,
                    b.last_line,
                ),
            )
            where += ": similar to " + _span(best.path, best.first_line, best.last_line, relative)
            if len(peers) > 1:
                where += f" and {_plural(len(peers) - 1, 'other place')}"
        lines.append(f"    {where}")
    return lines, total - len(shown)


def left_alone_lines(
    report: "RunReport", relative: Callable[[str], str], *, pairs: bool = True
) -> List[str]:
    """The five largest places in the default report.

    ``pairs`` leaves out the declined pairs when false (``shows_pairs``).
    """
    pair_reasons = set(report.declined_pairs)
    located: List["LeftAlone"] = sorted(
        (
            site
            for site in report.left_alone
            if site.blocks and (pairs or site.reason not in pair_reasons)
        ),
        key=location_rank,
    )
    if not located:
        return []
    shown = located[:LARGEST_SHOWN]
    total = sum(site.occurrences for site in located) + (report.unlisted_pairs if pairs else 0)
    heading = (
        "Places left alone, largest first:"
        if total <= LARGEST_SHOWN
        else f"The {LARGEST_SHOWN} largest of {total} places left alone"
        " (--explain shows more, grouped by reason):"
    )
    lines = ["", heading]
    remaining = LARGEST_SHOWN
    for site in shown:
        if not remaining:
            break
        spans = [_span(b.path, b.first_line, b.last_line, relative) for b in site.blocks]
        where = spans[0] if len(spans) == 1 else ", ".join(spans[:-1]) + " and " + spans[-1]
        count = min(remaining, site.occurrences)
        lines.extend(repeat(f"  {where}: {explain(site.reason).sentence}", count))
        remaining -= count
    return lines


CROSS_MODULE_REMEDY = (
    "A module can share a helper with another only if one of them already imports the other,"
    " unconditionally, at its top, before its first definition; the helper then goes into the"
    " module imported. Adding that import is the one change that lets Towel share such code."
)


def shared_across_modules(changes: Iterable["AppliedChange"]) -> bool:
    """Whether a run's applied changes call one helper from two modules."""
    paths: Dict[str, Set[str]] = {}
    for change in changes:
        paths.setdefault(change.helper, set()).add(change.path)
    return any(len(where) > 1 for where in paths.values())


def cross_module_lines(
    report: "RunReport", relative: Callable[[str], str], *, explaining: bool = False
) -> List[str]:
    """When ``--cross-module`` shared nothing: what each pair of modules lacked, and the remedy.

    One line per pair of modules and condition, however many pairs of
    blocks met it; nothing when no pair across modules was declined for a
    reason only such a pair can meet.
    """
    found: Dict[Tuple[str, str, str], None] = {}
    # Adding an import cannot help a pair that already imports each other, so
    # the remedy is withheld when any declined pair met an import cycle. The
    # full reason counts decide it, not the sampled locations.
    has_cycle = any(
        reason_code(reason) == RejectReason.IMPORT_CYCLE
        for reason in (*report.declined_pairs, *report.declined_proposals)
    )
    sites = report.left_alone
    if report.module_conditions is not None:
        for a, b, why in report.module_conditions:
            first, second = sorted((relative(a), relative(b)))
            if first != second:
                found[first, second, why] = None
        # Pair conditions were collected in settlement order, independently
        # of which pairs the list of largest places can show.
        proposals = sum(report.declined_proposals.values())
        sites = sites[-proposals:] if proposals else ()
    for site in sites:
        paths = sorted({relative(block.path) for block in site.blocks})
        condition = cross_module_condition(site.reason)
        if len(paths) < 2 or condition is None:
            continue
        found[(paths[0], " and ".join(paths[1:]), condition)] = None
    if not found:
        return []
    remedy = [] if has_cycle else [CROSS_MODULE_REMEDY]
    if explaining:
        return [" ".join(["--cross-module shared nothing between modules.", *remedy])]
    return [
        "",
        "--cross-module shared nothing between modules. What each pair of modules lacked:",
        *(f"  {first} and {others}: {condition}." for first, others, condition in found),
        *remedy,
    ]


def _not_examined_record(entry: NotExamined, relative: Callable[[str], str]) -> NotExaminedRecord:
    record: NotExaminedRecord = {
        "path": relative(entry.path),
        "module_statements": entry.module_statements,
        "early_return_guards": entry.early_return_guards,
    }
    if entry.limited_functions:
        record["limited_functions"] = [
            {
                "function": item.function,
                "line": item.line,
                "windows": item.windows,
                "limit": item.limit,
            }
            for item in entry.limited_functions
        ]
    return record


def analysed_record(report: "RunReport", relative: Callable[[str], str]) -> AnalysedRecord:
    """The census as the JSON result reports it."""
    record: AnalysedRecord = {
        "files": report.files_analysed,
        "functions": report.functions_analysed,
        "skipped": [
            {"path": relative(skipped.path), "reason": skipped.reason}
            for skipped in report.skipped_files
        ],
        "not_examined": [_not_examined_record(entry, relative) for entry in report.not_examined],
    }
    if report.resource_limit_reached:
        record["resource_guard"] = _RESOURCE_LIMIT_NOTE
    return record
