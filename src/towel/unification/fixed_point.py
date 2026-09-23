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

"""The fixed-point drivers: apply one proposal, re-analyze, repeat.

The single-file loop re-analyzes one module after each application. The
directory loop analyzes the whole project, applies the best proposal,
re-analyzes the files it rewrote for localized follow-ups, and when that
queue drains re-pairs changed files and previously declined proposals.
An unchanged project with only declined proposals is a fixed point.
The run stops there or when the iteration bound is reached. Before a run, modules that
inspect their own frames are named in a warning. Progress follows the
progress mode: ``tqdm`` (a bar when tqdm is installed; without it a warning
once per process, an inline bar on stderr for the pairing loop and none for
the apply phase), ``auto`` (the tqdm bar, or an inline bar on stderr for
both), ``detail`` (a line per phase), or ``none``.
"""

from __future__ import annotations

from contextlib import contextmanager
import dataclasses
import logging
import os
from dataclasses import dataclass, field
import textwrap
import threading
import time

from pathlib import Path
import ast
import sys
from typing import (
    Callable,
    Iterator,
    Literal,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
)
from .defaults import DEFAULT_MAX_ITERATIONS
from .exceptions import CheckerUnavailableError, RefactoringError
from .models import RefactoringProposal, TerminationReason
from .overlap import filter_overlapping_proposals
from .progress import (
    DEFAULT_PROGRESS,
    Heartbeat,
    ProgressBar,
    ProgressBarFactory,
    ProgressMode,
    load_tqdm,
    normalize_progress,
    quietly,
    render_inline_bar,
    wants_bar,
)
from .semantic_safety import frame_sensitivity_markers
from towel.changes import ChangePlan, StaleSource, apply_changes
from ..consumers import MAXIMUM_FILES
from ..diagnostics import LOG, OVERLAP, REJECTIONS, TYPES, UNIFIER, VALIDATION, debugging
from ..filesystem import (
    StagedProject,
    copy_project,
    refuse_unusable_output,
    staged_changes,
    staged_project,
)
from ..project_layout import find_project_root
from ..source_text import UnencodableText, decode_source, encode_like, read_source
from ..type_inference import relocate_oracle

from .annotation_wiring import UNTYPED_REMEDY
from .materialize import Materialization

_TQDM_NOTED = False


def _note_missing_tqdm() -> None:
    """Say once per process that the bar the mode asked for is not installed."""
    global _TQDM_NOTED
    if not _TQDM_NOTED:
        _TQDM_NOTED = True
        LOG.warning(
            "tqdm is not installed; the pairing phase shows an inline bar on stderr, the others none"
        )


def _is_checker_failure(error: BaseException) -> bool:
    return isinstance(error, CheckerUnavailableError)


DeclineReason = Literal[
    "refused by the type checker",
    "not judged: the type checker could not run",
    "not representable in its file's encoding",
    "could not be rendered",
    "changed nothing",
]
"""Why a proposal the analysis built was not applied."""


@dataclass(frozen=True)
class RunReport:
    """Why the last fixed-point run did not do more, as counts a reader can act on.

    ``declined_pairs`` counts the candidate pairs the run's last whole analysis
    declined, by ``RejectReason``; a pair that only repeated another pair's
    proposal is not counted, since nothing was lost. ``declined_proposals``
    counts the proposals built but not applied, by ``DeclineReason``, each
    heard once however often the run reconsidered it.
    """

    declined_pairs: Mapping[str, int] = field(default_factory=dict)
    declined_proposals: Mapping[DeclineReason, int] = field(default_factory=dict)


_Reason = TypeVar("_Reason", bound=str)


def counted_reasons(counts: Mapping[_Reason, int]) -> str:
    """``reason count`` for each reason, most frequent first: ``import_cycle 1``."""
    return ", ".join(
        f"{reason} {count}"
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


class FixedPointDrivers(Materialization):
    """FixedPointDrivers methods of the engine; see the module docstring."""

    # Proposals the last run dropped because the checker could not run, by
    # identity, and the last reason it gave.
    _unchecked: FrozenSet[str] = frozenset()
    _unchecked_reason: str = ""
    # Every proposal the last run built and did not apply, by identity, with
    # the latest reason; and what its last whole analysis declined.
    _declined: Dict[str, DeclineReason] = {}
    _last_declined_pairs: Dict[str, int] = {}

    @property
    def run_report(self) -> RunReport:
        """Why the last fixed-point run declined what it did; see ``RunReport``."""
        proposals: Dict[DeclineReason, int] = {}
        for reason in self._declined.values():
            proposals[reason] = proposals.get(reason, 0) + 1
        return RunReport(dict(self._last_declined_pairs), proposals)

    def _note_analysis(self, analyzed: Path, reporter: Optional["_ApplyProgress"] = None) -> None:
        """Keep what a whole analysis of ``analyzed`` declined, for the run's report."""
        self._last_declined_pairs = dict(self._pair_rejections)
        if reporter is not None and self._last_declined_pairs:
            reporter.detail(
                f"Declined {sum(self._last_declined_pairs.values())} candidate pair(s): "
                f"{counted_reasons(self._last_declined_pairs)}"
            )

    def _decline(self, proposal: RefactoringProposal, reason: DeclineReason) -> None:
        self._declined = {**self._declined, _RejectedProposals.identity(proposal): reason}

    def _applied(self, proposal: RefactoringProposal) -> None:
        """A proposal declined earlier and applied since, at a rehearing, was not declined."""
        identity = _RejectedProposals.identity(proposal)
        if identity in self._declined:
            self._declined = {key: why for key, why in self._declined.items() if key != identity}

    @property
    def checker_failures(self) -> int:
        """How many proposals the last fixed-point run dropped because the checker could not run.

        Such a proposal was not refused on its merits: nothing is known about
        it. A run in which that happened and nothing was applied raises
        instead of reporting a fixed point it never reached.
        """
        return len(self._unchecked)

    def _begin_counting_checker_failures(self) -> None:
        self._unchecked, self._unchecked_reason = frozenset(), ""
        self._declined, self._last_declined_pairs = {}, {}

    def _refuse_a_run_the_checker_emptied(self, applied: int) -> None:
        """Raise when nothing was applied and the checker failed on some proposal.

        Reporting "no refactorings found" there would read as a verdict on the
        project, and exiting cleanly would hide a checker that timed out or
        crashed on every candidate it was given.
        """
        if applied or not self._unchecked:
            return
        raise RefactoringError(
            f"Nothing was applied, and the type checker could not run for "
            f"{len(self._unchecked)} proposal(s), which were therefore not judged: "
            f"{self._unchecked_reason}\nFix what stops the checker, or {UNTYPED_REMEDY}"
        )

    @staticmethod
    def _pop_next_proposal(queue: List[RefactoringProposal]) -> Optional[RefactoringProposal]:
        """Remove and return the oldest queued proposal."""

        if not queue:
            return None
        return queue.pop(0)

    def _resolve_progress_backend(
        self, progress: ProgressMode
    ) -> Tuple[ProgressMode, Optional[ProgressBarFactory]]:
        """The normalized progress mode and, when the mode wants tqdm and it loads, its factory."""
        normalized = normalize_progress(progress)
        factory = load_tqdm() if normalized in {"auto", "tqdm"} else None
        if normalized == "tqdm" and factory is None:
            _note_missing_tqdm()
        return normalized, factory

    def refactor_to_fixed_point(
        self,
        file_path: str,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        progress: ProgressMode = DEFAULT_PROGRESS,
        *,
        output_path: Optional[str] = None,
    ) -> Tuple[str, int, List[str]]:
        """Apply refactorings to one file, one at a time, until none remain.

        Each application re-analyzes the file, so every proposal is computed
        against the current text. A file that does not decode or parse is
        reported as a ``ValueError`` naming it.

        Args:
            file_path: The file to refactor. It is refactored inside a
                private copy of its whole project, and rewritten, as one
                journaled change, only once the run has succeeded; a run that
                fails or is interrupted leaves it as it was.
            max_iterations: Stop after this many applied refactorings; 0 or
                less runs to a fixed point.
            progress: How progress is shown: ``tqdm`` (a bar when tqdm is
                installed), ``auto`` (that, or an inline bar), ``detail``
                (a line per proposal), or ``none``.
            output_path: An optional new file to write the result to instead,
                when the run succeeds; the original is then only read.

        Returns:
            The final source, the number of refactorings applied, and their
            descriptions in application order.
        """
        self._change_log = []
        self._begin_counting_checker_failures()
        analysis_progress: ProgressMode = progress if wants_bar(progress) else "none"
        current_bytes = Path(file_path).read_bytes()
        try:
            current_code = decode_source(current_bytes)
            ast.parse(current_code, filename=file_path)
        except UnicodeDecodeError as error:
            raise ValueError(f"{file_path}: {error}") from error
        except SyntaxError as error:
            raise ValueError(f"{file_path}: line {error.lineno}: {error.msg}") from error
        in_place = output_path is None or Path(output_path).resolve() == Path(file_path).resolve()
        if not in_place:
            refuse_unusable_output(Path(file_path), Path(str(output_path)))
        self.begin_refactoring_run([file_path])
        destination = Path(file_path) if in_place else Path(str(output_path))
        with self._staged_output(Path(file_path), destination) as stage:
            refactored = self._refactor_file_in_place(
                str(stage.target), current_bytes, current_code, max_iterations, analysis_progress
            )
            self._publish(stage)
            return refactored

    @staticmethod
    def _publish(stage: StagedProject, *, allow_empty: bool = False) -> None:
        """Hand a finished, confirmed run to the user: the output, or the project itself.

        Nothing the user owns has been touched before this. An in-place run's
        combined change is one journaled plan, so an interruption part way
        through it is rolled back or recoverable with ``towel recover``.
        """
        if stage.in_place:
            apply_changes(staged_changes(stage))
        else:
            copy_project(stage.target, stage.output, allow_empty=allow_empty)

    def _refactor_file_in_place(
        self,
        file_path: str,
        current_bytes: bytes,
        current_code: str,
        max_iterations: int,
        analysis_progress: ProgressMode,
    ) -> Tuple[str, int, List[str]]:
        """The single-file loop over ``file_path``, which holds ``current_bytes``."""
        self._warn_about_frame_sensitive_files(file_path)
        num_applied = 0
        descriptions = []
        rejected = _RejectedProposals()
        changed_since_hearing = False

        iteration = 0
        while True:
            # Analyze for refactoring opportunities
            # Important: invalidate cached analysis for this path so we see latest edits
            proposals = self.analyze_files(
                [file_path], invalidate_paths=[file_path], progress=analysis_progress
            )
            self._note_analysis(Path(file_path))

            if not proposals:
                # Fixed point reached - no more refactorings found
                break

            applied_one = False
            for proposal in proposals:
                if proposal in rejected:
                    continue
                recorded = len(self._change_log)
                rendered = self._rendered_or_none(file_path, proposal, current_bytes)
                if rendered is None:
                    rejected.add(proposal)
                    continue
                if rendered == current_code:
                    self._forget_records_since(recorded)
                    self._decline(proposal, "changed nothing")
                    continue
                new_code = rendered
                try:
                    apply_changes(
                        ChangePlan.from_sources({file_path: current_bytes}, {file_path: new_code})
                    )
                except BaseException:
                    self._forget_records_since(recorded)
                    raise
                self._applied(proposal)
                applied_one = True
                break
            if not applied_one:
                if rejected and changed_since_hearing:
                    # The file has changed since these were rejected; hear them once more.
                    rejected.clear()
                    changed_since_hearing = False
                    continue
                # Every proposal failed to render or changed nothing: a fixed point.
                break
            changed_since_hearing = True
            current_code = new_code
            current_bytes = encode_like(current_bytes, new_code)
            num_applied += 1
            descriptions.append(proposal.description)

            iteration += 1
            if max_iterations > 0 and iteration >= max_iterations:
                break

        if num_applied:
            self.confirm_run_with_a_cold_checker([file_path])
        self._refuse_a_run_the_checker_emptied(num_applied)
        return current_code, num_applied, descriptions

    def _rendered_or_none(
        self, file_path: str, proposal: RefactoringProposal, original: bytes
    ) -> Optional[str]:
        """The file with ``proposal`` applied, or None when rendering it fails.

        A proposal the materializer or the compiler rejects is a defect in
        the rendering of that one extraction; it is dropped with a warning
        and the run goes on, rather than aborting after whatever was applied
        before it. So is one whose text the file's encoding (``original``'s)
        cannot hold.
        """
        recorded = len(self._change_log)
        self._checker_refusals = 0
        try:
            new_code = self.apply_refactoring(file_path, proposal)
            compile(new_code, file_path, "exec")
            encode_like(original, new_code)
        except (RefactoringError, SyntaxError, ValueError) as error:
            self._forget_records_since(recorded)
            self._report_dropped(proposal, error)
            return None
        return new_code

    def _forget_records_since(self, recorded: int) -> None:
        """Drop the call-site records of a proposal that was rendered but not applied.

        Rendering records each call site it rewrites, because a name is only
        final once rendered; whether the result is written is decided after
        that. A record left behind describes a helper that is not in the output,
        and the sidecar offered it to the naming step as though it were.
        """
        del self._change_log[recorded:]

    def _report_dropped(self, proposal: RefactoringProposal, error: BaseException) -> None:
        """Say why ``proposal`` was not applied, and count it under that reason.

        Told apart by type, never by wording: a checker that could not run
        judged nothing; one that refused a rendered variant judged the proposal
        (``_checker_refusals`` counts those since the driver started it);
        text its file's encoding cannot hold is a limit of that file; anything
        else is a rendering Towel could not produce.
        """
        reason: DeclineReason
        if _is_checker_failure(error):
            self._unchecked = self._unchecked | {_RejectedProposals.identity(proposal)}
            self._unchecked_reason = str(error)
            reason, said = (
                "not judged: the type checker could not run",
                "the type checker could not check",
            )
        elif isinstance(error, RefactoringError) and self._checker_refusals:
            reason, said = "refused by the type checker", "the type checker refused"
        elif isinstance(error, UnencodableText):
            reason, said = (
                "not representable in its file's encoding",
                "its file's encoding cannot hold",
            )
        else:
            reason, said = "could not be rendered", "could not be rendered"
        self._decline(proposal, reason)
        LOG.warning("Dropped a proposal %s (%s): %s", said, proposal.description, error)
        if debugging(REJECTIONS):
            REJECTIONS.debug("RENDER FAILED: %s :: %r", proposal.description, error)

    _FRAME_SENSITIVE_DESCRIPTION = {
        "frame": "inspect call frames",
        "traceback": "read exception tracebacks",
        "stacklevel-warning": "attribute warnings to a caller's frame",
        "source": "read Python source text",
    }

    def _warn_about_frame_sensitive_files(self, directory: str) -> None:
        """Warn that some modules observe frames, tracebacks, or their own source.

        Extraction adds a helper frame and shifts line numbers, so a program
        that reads any of these can observe the change even when the result it
        computes is unchanged (pluggy attributes a warning through the new
        frame, lark's standalone tool copies marked source regions, glom and
        rich assert on rendered tracebacks). The scan names files to review;
        it is not a proof of breakage, and a callee that inspects frames
        internally is invisible to it. Emitted on stderr like any diagnostic.
        """
        flagged: List[Tuple[str, FrozenSet[str]]] = []
        for path in self._find_python_files(directory):
            try:
                markers = frame_sensitivity_markers(read_source(path))
            except (OSError, UnicodeError, SyntaxError):
                # An unreadable file is reported by the analysis that follows.
                continue
            if markers:
                flagged.append((path, markers))
        if not flagged:
            return
        kinds = sorted({m for _path, markers in flagged for m in markers})
        described = ", ".join(self._FRAME_SENSITIVE_DESCRIPTION.get(k, k) for k in kinds)
        directory_root = Path(directory)
        lines = [
            f"warning: {len(flagged)} module(s) in this project {described}; a "
            "transformation that adds a helper frame or shifts line numbers may "
            "change their observable behavior even when it preserves the "
            "program's result. Review these files' diffs or pass --exclude:"
        ]
        for path, markers in sorted(flagged):
            try:
                shown = str(Path(path).relative_to(directory_root))
            except ValueError:
                shown = path
            lines.append(f"    {shown} ({', '.join(sorted(markers))})")
        LOG.warning("\n".join(lines))

    def refactor_directory_to_fixed_point(
        self,
        input_dir: str,
        output_dir: str,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        progress: ProgressMode = DEFAULT_PROGRESS,
    ) -> Tuple[Dict[str, Tuple[int, List[str]]], TerminationReason]:
        """
        Apply refactorings across a directory (recursively) until a fixed point.

        The first pass analyzes the whole project, so same-file and cross-file
        proposals both apply. After each applied proposal the files it rewrote
        are re-analyzed for localized follow-ups; when that queue drains the
        project is re-paired, considering rewritten files and proposals
        declined in the previous project context. The run stops when no
        proposal applies or the iteration bound is reached.

        Args:
            input_dir: The directory to analyze.
            output_dir: The directory to write into, or ``input_dir`` itself
                to refactor in place. Either way ``input_dir`` is refactored
                inside a private copy of its whole project, and nothing is
                written until the run has succeeded: then its refactored
                counterpart is written here all at once, or, in place, every
                file it rewrote is changed as one journaled plan. The returned
                paths name files here.
            max_iterations: Stop after this many applied refactorings; 0 or
                less runs to a fixed point.
            progress: How progress is shown: ``tqdm`` (a bar when tqdm is
                installed), ``auto`` (that, or an inline bar), ``detail``
                (a line per proposal), or ``none``.

        Returns:
            (results_dict, termination_reason)
            termination_reason ∈ {"fixed_point", "iteration_cap"}
        """
        self._change_log = []
        self._begin_counting_checker_failures()
        reporter = _ApplyProgress(*self._resolve_progress_backend(progress))

        input_path = Path(input_dir)
        output_path = Path(output_dir)
        resolved_input = input_path.resolve()
        resolved_output = output_path.resolve()
        if resolved_input != resolved_output:
            if (
                resolved_input in resolved_output.parents
                or resolved_output in resolved_input.parents
            ):
                raise ValueError("Input and output must not contain one another")
            if output_path.exists() and any(output_path.iterdir()):
                raise ValueError("Output directory must be empty")

        if not input_path.is_dir():
            raise ValueError("Input directory does not exist")
        if resolved_input != resolved_output:
            refuse_unusable_output(input_path, output_path, allow_empty=True)
        self.begin_refactoring_run(self._find_python_files(input_dir))
        destination = input_path if resolved_input == resolved_output else output_path
        with self._staged_output(input_path, destination) as stage:
            self._analysis_paths = tuple(self._find_python_files(str(stage.target)))
            results, termination_reason = self._refactor_directory_in_place(
                stage.target, reporter, max_iterations
            )
            self._publish(stage, allow_empty=True)
            return {stage.public(path): result for path, result in results.items()}, (
                termination_reason
            )

    def _refactor_directory_in_place(
        self, directory: Path, reporter: "_ApplyProgress", max_iterations: int
    ) -> Tuple[Dict[str, Tuple[int, List[str]]], TerminationReason]:
        """The directory loop over ``directory``, then the cold confirmation of what it did."""
        self._warn_about_frame_sensitive_files(str(directory))

        run = _DirectoryRun()
        termination_reason: TerminationReason = "fixed_point"

        try:
            outcome = self._apply_until_fixed_point(
                directory, run, reporter, max_iterations, termination_reason
            )
        finally:
            # A display thread must not outlive the run, however it ended.
            reporter.close()
        # Only a run that finished is confirmed: one that failed is published
        # nowhere, and nothing it applied reached the user.
        if run.applied:
            self.confirm_run_with_a_cold_checker(self._find_python_files(str(directory)))
        self._refuse_a_run_the_checker_emptied(run.applied)
        return outcome

    @contextmanager
    def _staged_output(self, origin: Path, output: Path) -> Iterator[StagedProject]:
        """Refactor ``origin``'s counterpart in a private copy of its whole project.

        A run reads the rest of the project as it refactors: the import graph
        that the cycle and import-time-effect guards walk, the packaging that
        names modules, the configuration. A copy of the target alone has none
        of it, so a cycle through a module outside the target went unseen and
        the adopted output could not be imported. Staging the whole project
        gives every run the same view: the target's counterpart is refactored
        in the stage, with the checker reading the stage under the original's
        names, and the caller publishes it -- to ``output``, or, when
        ``output`` is ``origin``, back over the project -- only once the run
        and its cold confirmation have succeeded, so a failed run leaves
        nothing behind. An in-place run used to write each refactoring as it
        went, and a confirmation that then refused the result left it written.
        Every path the run reports names the output (or, outside the target,
        the original), not the stage, which is removed however the block ends.
        """
        inner_oracle = self._type_run_oracle
        root = find_project_root(origin)
        with staged_project(root, origin, output, limit=MAXIMUM_FILES) as stage:
            # Names are the program's, read from the project itself rather
            # than from its copy (``ProgramImports``). A run that may share a
            # helper across modules reads them now, before any pair is judged
            # or a worker forked; one that may not, only when a question needs
            # them: what an existing import binds, or how to spell the
            # type-only import a helper's annotation needs.
            self.import_graph.begin_run(stage.origin_root, stage.root)
            if self.cross_module_helpers:
                self.import_graph.program_for(stage.target)
            if inner_oracle is not None:
                # Only the target: the rest of the stage is the original, byte
                # for byte, and restating it to the checker would make it
                # check modules the project's configuration leaves out, whose
                # errors the original check never saw.
                self._type_run_oracle = relocate_oracle(
                    inner_oracle, stage.origin_target, stage.target
                )
            self._output_origin = (stage.origin_root, stage.root)
            self._analysis_paths = (str(stage.target),)
            rewrite = _StagePathsInLogs(stage.public_text)
            loggers = (LOG, REJECTIONS, VALIDATION, OVERLAP, TYPES, UNIFIER)
            for logger in loggers:
                logger.addFilter(rewrite)
            try:
                yield stage
                self._change_log = [
                    dataclasses.replace(change, path=stage.public(change.path))
                    for change in self._change_log
                ]
            except (OSError, ValueError, RefactoringError) as error:
                _name_public_paths(error, stage.public_text)
                raise
            finally:
                for logger in loggers:
                    logger.removeFilter(rewrite)
                # The stage is about to go; nothing may keep checking against it.
                self._type_run_oracle = inner_oracle
                self._output_origin = None
                self.import_graph.begin_run()

    def _apply_until_fixed_point(
        self,
        output_path: Path,
        run: "_DirectoryRun",
        reporter: "_ApplyProgress",
        max_iterations: int,
        termination_reason: TerminationReason,
    ) -> Tuple[Dict[str, Tuple[int, List[str]]], TerminationReason]:
        """Apply queued proposals, re-pairing the project, until none applies."""
        proposal_queue: List[RefactoringProposal] = []
        global_passes = 0
        iterations = 0
        # Main loop -------------------------------------------------------
        while True:
            if not proposal_queue:
                found = self._global_pass(output_path, run, global_passes, reporter)
                global_passes += 1
                if found is None and run.owes_a_rehearing():
                    # Nothing more applies, but proposals were declined along
                    # the way and the project has changed since. They are heard
                    # once more, against it as it now stands, before the run
                    # calls itself finished. A rehearing needs an application
                    # since the last one, so the run cannot circle on proposals
                    # the project keeps refusing.
                    run.begin_rehearing()
                    reporter.detail("Rehearing proposals declined earlier")
                    found = self._global_pass(
                        output_path, run, global_passes, reporter, rehearing=True
                    )
                    global_passes += 1
                if found is None:
                    reporter.finish_at_fixed_point()
                    break
                proposal_queue = found
                reporter.discovered(proposal_queue, run.applied, max_iterations)

            if not proposal_queue:
                break

            proposal = self._pop_next_proposal(proposal_queue)
            if proposal is None:
                break
            if proposal in run.rejected:
                if debugging(REJECTIONS):
                    REJECTIONS.debug("ALREADY REJECTED THIS PASS: %s", proposal.description)
                continue
            last_desc = proposal.description
            reporter.applying(run.applied, len(proposal_queue), iterations + 1, last_desc)

            # Apply proposal. A proposal computed before an earlier application
            # changed one of its files is stale: drop it and re-analyze those
            # files so a fresh proposal can take its place.
            try:
                refreshed = self._apply_and_refresh(proposal, proposal_queue, run, reporter)
                if refreshed is None:
                    # Nothing was written, so nothing was applied: counting it
                    # would earn a rehearing the project has not changed for.
                    continue
                proposal_queue = refreshed
            except (RefactoringError, SyntaxError, UnencodableText) as error:
                run.deferred_paths.update(
                    os.path.abspath(path)
                    for path in {
                        proposal.file_path,
                        *(rep.file_path or proposal.file_path for rep in proposal.replacements),
                    }
                )
                run.rejected.add(proposal)
                self._report_dropped(proposal, error)
                continue
            except StaleSource as conflict:
                stale_paths = sorted({path for path, _ in proposal.source_digests})
                reporter.detail(
                    f"Dropped stale proposal ({conflict}); re-analyzing {len(stale_paths)} file(s)"
                )
                if debugging(REJECTIONS):
                    REJECTIONS.debug(
                        "STALE: %s :: paths=%s :: replacements=%s",
                        proposal.description,
                        stale_paths,
                        [rep.file_path or proposal.file_path for rep in proposal.replacements],
                    )
                self.invalidate_paths(stale_paths)
                run.changed(stale_paths)
                proposal_queue = [
                    p
                    for p in proposal_queue
                    if not ({path for path, _ in p.source_digests} & set(stale_paths))
                ]
                refreshed = self.analyze_files(
                    stale_paths, invalidate_paths=stale_paths, progress=reporter.analysis_mode
                )
                if refreshed:
                    proposal_queue = filter_overlapping_proposals(refreshed) + proposal_queue
                continue

            iterations += 1
            run.applied += 1
            reporter.applied(run.applied, len(proposal_queue), iterations, last_desc)

            if max_iterations > 0 and iterations >= max_iterations:
                termination_reason = "iteration_cap"
                reporter.finish_at_cap()
                break

        return run.results, termination_reason

    def _global_pass(
        self,
        output_path: Path,
        run: "_DirectoryRun",
        passes_so_far: int,
        reporter: "_ApplyProgress",
        rehearing: bool = False,
    ) -> Optional[List[RefactoringProposal]]:
        """Analyze the whole directory and queue its non-overlapping proposals; None at the fixed point.

        After the first pass, re-pair rewritten files and proposals deferred
        by rendering or checking. A changed project can make a deferred
        proposal valid, but an unchanged project cannot justify another pass.
        A proposal the project declined is heard again only at a rehearing,
        which is what the run does instead of stopping; until then every
        analysis finds it and is not allowed to retry it.
        """
        if run.global_revision == run.revision and not rehearing:
            return None
        reporter.announce_analysis(output_path)
        restrict = (
            frozenset(run.changed_since_global | run.deferred_paths)
            if self.incremental_global_passes and passes_so_far > 0 and not rehearing
            else None
        )
        proposals = self.analyze_directory(
            str(output_path),
            recursive=True,
            progress=reporter.analysis_mode,
            changed_files=restrict,
        )
        self._note_analysis(output_path, reporter)
        run.global_revision = run.revision
        run.changed_since_global.clear()
        if not proposals:
            return None
        return filter_overlapping_proposals(proposals)

    def _apply_and_refresh(
        self,
        proposal: RefactoringProposal,
        queue: List[RefactoringProposal],
        run: "_DirectoryRun",
        reporter: "_ApplyProgress",
    ) -> Optional[List[RefactoringProposal]]:
        """Apply ``proposal``, invalidate what it touched, and refresh ``queue``.

        None when the proposal rendered the bytes the files already held, so
        nothing was written and nothing was applied.

        Queued proposals that were computed against a rewritten file are
        dropped; the rewritten files are re-analyzed at once and any follow-up
        that touches them goes to the front of the queue.
        """
        before = {
            path: Path(path).read_bytes()
            for path in {
                proposal.file_path,
                *(rep.file_path or proposal.file_path for rep in proposal.replacements),
            }
        }
        recorded = len(self._change_log)
        self._checker_refusals = 0
        try:
            modified_files = self.apply_refactoring_multi_file(proposal)
            plan = ChangePlan.from_sources(before, modified_files)
            # A plan is empty when every file renders the bytes it already
            # holds. Counting that as applied would advance the run's revision
            # without changing the project, so the next analysis would find the
            # proposal again, render the same bytes, and the run would never
            # end. It is remembered as declined instead: the same input renders
            # the same output, so hearing it again before the project changes
            # is pointless.
            if not plan.changes:
                self._forget_records_since(recorded)
                self._decline(proposal, "changed nothing")
                run.rejected.add(proposal)
                reporter.detail(f"Proposal changed nothing: {proposal.description}")
                return None
            apply_changes(plan)
        except BaseException:
            self._forget_records_since(recorded)
            raise
        self._applied(proposal)
        # Every file the proposal rendered is re-analysed and recorded, not
        # only those whose bytes moved: a file rendered identically is still
        # one the proposal reached, and the localized pass that follows must
        # look at all of them.
        changed_paths = list(modified_files.keys())
        for fpath in modified_files:
            run.record(fpath, proposal.description)
        self.invalidate_paths(changed_paths)
        changed_set = set(map(str, changed_paths))
        queue = [p for p in queue if not ({path for path, _ in p.source_digests} & changed_set)]
        localized = self.analyze_files(
            list(changed_paths),
            invalidate_paths=list(changed_paths),
            progress=reporter.analysis_mode,
        )
        if localized:
            localized = [
                p
                for p in filter_overlapping_proposals(localized)
                if any((rep.file_path or p.file_path) in changed_set for rep in p.replacements)
            ]
        if localized:
            queue = localized + queue
            reporter.detail(f"Localized +{len(localized)} follow-up(s)")
            reporter.inline(run.applied, len(queue), "localized", f"+{len(localized)} follow-ups")
        return queue


class _StagePathsInLogs(logging.Filter):
    """Rewrite a record's stage paths to the ones the user knows, before any handler sees it."""

    def __init__(self, rewrite: Callable[[str], str]) -> None:
        super().__init__()
        self._rewrite = rewrite

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        rewritten = self._rewrite(message)
        if rewritten != message:
            record.msg, record.args = rewritten, None
        return True


def _name_public_paths(error: BaseException, rewrite: Callable[[str], str]) -> None:
    """Point a failure's message at the paths the user knows; the stage it names is gone."""
    if isinstance(error, OSError):
        for attribute in ("filename", "filename2"):
            value = getattr(error, attribute)
            if isinstance(value, str):
                setattr(error, attribute, rewrite(value))
    elif len(error.args) == 1 and isinstance(error.args[0], str):
        error.args = (rewrite(error.args[0]),)


class _RejectedProposals:
    """Proposals rejected since the project was last analyzed as a whole.

    Re-analyzing a rewritten file finds again every proposal in it that was
    rejected before, and a rejection costs a project check per annotation
    variant: on a capped Sphinx run 204 of 287 checks retried four proposals,
    and no proposal was ever accepted after being rejected. Nothing is lost by
    declining those retries, because the run does not end here: a changed
    project gets another whole analysis, which empties this and hears each
    proposal once more.

    A proposal is known by what it extracts and where it puts it, not by line
    numbers, which every earlier application shifts.
    """

    def __init__(self) -> None:
        self._identities: Set[str] = set()

    @staticmethod
    def identity(proposal: RefactoringProposal) -> str:
        return repr(
            (
                proposal.file_path,
                proposal.insert_into_class,
                proposal.insert_into_function,
                None if proposal.reused_function is None else proposal.reused_function.name,
                ast.dump(proposal.extracted_function),
                sorted(
                    repr((rep.file_path or proposal.file_path, rep.class_name, ast.dump(rep.node)))
                    for rep in proposal.replacements
                ),
            )
        )

    def add(self, proposal: RefactoringProposal) -> None:
        self._identities.add(self.identity(proposal))

    def __contains__(self, proposal: RefactoringProposal) -> bool:
        return self.identity(proposal) in self._identities

    def __bool__(self) -> bool:
        return bool(self._identities)

    def clear(self) -> None:
        self._identities.clear()


@dataclass
class _DirectoryRun:
    """What one directory run has done so far."""

    # Per file: how many proposals rewrote it, and their descriptions.
    results: Dict[str, Tuple[int, List[str]]] = field(default_factory=dict)
    # Files changed since the last global pass, by Towel or an external edit.
    changed_since_global: Set[str] = field(default_factory=set)
    # Rendering/checking can depend on other files, so retry these after any
    # project change even when their own source has not changed.
    deferred_paths: Set[str] = field(default_factory=set)
    rejected: "_RejectedProposals" = field(default_factory=lambda: _RejectedProposals())
    # How many refactorings had been applied when the declined proposals were
    # last heard again. Starting at zero is what makes a rehearing worth having:
    # nothing applied means nothing has changed for a declined proposal to be
    # reconsidered against.
    reheard_after: int = 0
    revision: int = 0
    global_revision: Optional[int] = None
    applied: int = 0

    def owes_a_rehearing(self) -> bool:
        """Whether anything was declined that the project has changed under since."""
        return bool(self.rejected) and self.applied != self.reheard_after

    def begin_rehearing(self) -> None:
        self.reheard_after = self.applied
        self.rejected.clear()

    def record(self, path: str, description: str) -> None:
        count, descriptions = self.results.get(path, (0, []))
        self.results[path] = (count + 1, descriptions + [description])
        self.changed([path])

    def changed(self, paths: Sequence[str]) -> None:
        """Record source changes from an application or stale-source recovery."""
        if paths:
            self.changed_since_global.update(map(os.path.abspath, paths))
            self.revision += 1


ProgressPhase = Literal["discovered", "localized", "apply", "applied"]


class _ApplyProgress:
    """How the directory driver reports progress: a tqdm bar, an inline bar, or detail lines.

    Which of the three applies is fixed by the progress mode and by whether
    tqdm loaded. The driver reports what happened and never asks which one
    is showing it. The tqdm bar is created only once there are proposals to
    apply, so a run never opens with an empty bar.
    """

    _LISTING_CAP = 25

    def __init__(self, mode: ProgressMode, factory: Optional[ProgressBarFactory]) -> None:
        self._mode = mode
        self._factory = factory
        self._bar: Optional[ProgressBar] = None
        # What the run is doing now, for a redraw that no progress prompted.
        self._doing = ""
        self._counts = (0, 0)
        self._drawing = threading.Lock()
        self._started = time.monotonic()
        self._heartbeat = Heartbeat(self._redraw)

    @property
    def _use_tqdm(self) -> bool:
        return self._factory is not None

    @property
    def analysis_mode(self) -> ProgressMode:
        """The mode each global analysis pass runs under: a bar when one is wanted."""
        return self._mode if wants_bar(self._mode) else "none"

    def _active_bar(self) -> Optional[ProgressBar]:
        return self._bar if self._use_tqdm else None

    def detail(self, message: str) -> None:
        """A line of the detail log, in detail mode only."""
        if self._mode == "detail":
            LOG.info("[towel] %s", message)

    def inline(self, applied: int, queued: int, phase: ProgressPhase, desc: str) -> None:
        """Redraw the inline bar; only the automatic mode without tqdm shows one."""
        if self._mode != "auto" or self._use_tqdm:
            return
        denom = max(applied + queued, 1)
        pct = int((applied / denom) * 100)
        bar = render_inline_bar(pct, bar_len=32)
        short = desc if len(desc) <= 40 else desc[:37] + "..."
        elapsed = time.monotonic() - self._started
        quietly(
            lambda: print(
                f"\r[towel] {phase:<10} [{bar}] {pct:3d}% "
                f"| applied={applied} queued={queued} | {elapsed / 60:5.1f}m | {short}",
                end="",
                flush=True,
                file=sys.stderr,
            )
        )

    def announce_analysis(self, output_path: Path) -> None:
        """Say how many files the coming global pass will analyze."""
        if self._mode == "none":
            return
        try:
            file_count = sum(1 for _ in output_path.rglob("*.py"))
        except OSError:
            # Only the directory walk for a progress message; a real I/O
            # problem will resurface in the analysis that follows.
            return
        self.detail(f"Analyzing {file_count} file(s)...")

    def discovered(
        self, queue: Sequence[RefactoringProposal], applied: int, max_iterations: int
    ) -> None:
        """A global pass queued ``queue``; list them in detail mode and open the bar."""
        self.detail(f"Discovered {len(queue)} proposal(s)")
        if self._mode == "detail":
            for position, proposal in enumerate(queue[: self._LISTING_CAP], 1):
                short = textwrap.shorten(proposal.description, width=100, placeholder="...")
                LOG.info("    %2d. %s", position, short)
            if len(queue) > self._LISTING_CAP:
                LOG.info("    ... %d more", len(queue) - self._LISTING_CAP)
        self.inline(applied, len(queue), "discovered", "proposals queued")
        if self._factory is not None and self._bar is None:
            try:
                # leave=False, so later prints do not duplicate the bar line.
                self._bar = self._factory(
                    total=max_iterations if max_iterations > 0 else None,
                    desc="apply",
                    unit="it",
                    dynamic_ncols=True,
                    leave=False,
                )
                self._postfix(0, len(queue))
            except Exception:
                # A bar that cannot be created costs only its display.
                self._bar = None
        if wants_bar(self._mode) and queue:
            self._heartbeat.start()

    def applying(self, applied: int, queued: int, iteration: int, desc: str) -> None:
        """About to weigh the ``iteration``-th proposal, which may take a while.

        Verifying one proposal costs a whole-project check per candidate
        signature and advances nothing, so this is where a run looks stalled.
        The bar says which proposal is being weighed and the heartbeat keeps
        its clock moving until the answer comes back.
        """
        with self._drawing:
            self._doing, self._counts = f"#{iteration}: {desc}", (applied, queued)
        bar = self._active_bar()
        if bar is None:
            self.inline(applied, queued, "apply", self._doing)
            return
        self._redraw()

    def _redraw(self) -> None:
        """Show what is happening now; called by the heartbeat as well as by the run."""
        with self._drawing:
            applied, queued = self._counts
            bar = self._active_bar()
            if bar is None:
                if self._doing:
                    self.inline(applied, queued, "apply", self._doing)
                return
            short = self._doing if len(self._doing) <= 44 else self._doing[:41] + "..."
            quietly(lambda: bar.set_postfix({"A": applied, "Q": queued, "on": short}, refresh=True))

    def applied(self, applied: int, queued: int, iteration: int, desc: str) -> None:
        """The ``iteration``-th proposal was applied."""
        with self._drawing:
            self._doing, self._counts = f"#{iteration}: {desc}", (applied, queued)
        bar = self._active_bar()
        if bar is None:
            self.inline(applied, queued, "applied", self._doing)
            return
        with self._drawing:
            quietly(lambda: bar.update(1))
        self._redraw()

    def _postfix(self, applied: int, queued: int) -> None:
        self._counts = (applied, queued)
        self._redraw()

    def close(self) -> None:
        """Release the display; safe to call more than once and after a failure.

        A run that ends by raising never reaches ``finish_at_fixed_point``, and
        a tqdm bar left open writes over whatever the terminal prints next.
        """
        self._heartbeat.stop()
        bar, self._bar = self._bar, None
        if bar is not None:
            quietly(bar.close)

    def finish_at_fixed_point(self) -> None:
        """No proposals remain: close the bar, or end the inline bar's line."""
        self._heartbeat.stop()
        bar = self._active_bar()
        if bar is not None:
            self._bar = None  # Closed here, so ``close`` has nothing left to do.

            def finish() -> None:
                bar.refresh()
                bar.close()

            quietly(finish)
        elif wants_bar(self._mode) and not self._use_tqdm:
            print(file=sys.stderr)

    def finish_at_cap(self) -> None:
        """The iteration cap was reached: close the bar, or end the inline bar's line."""
        self._heartbeat.stop()
        bar = self._active_bar()
        if bar is not None:
            self._bar = None
            quietly(bar.close)
        elif wants_bar(self._mode) and not self._use_tqdm:
            print(file=sys.stderr)
