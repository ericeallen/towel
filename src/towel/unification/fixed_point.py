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

import os
from dataclasses import dataclass, field
import textwrap
import threading
import time

from pathlib import Path
import ast
import sys
from typing import Literal, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple
from .defaults import DEFAULT_MAX_ITERATIONS
from .exceptions import RefactoringError
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
from ..diagnostics import LOG, REJECTIONS, debugging
from ..filesystem import copy_project
from ..source_text import decode_source, encode_like, read_source
from ..type_inference import relocate_oracle

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


class FixedPointDrivers(Materialization):
    """FixedPointDrivers methods of the engine; see the module docstring."""

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
            file_path: The file to refactor in place.
            max_iterations: Stop after this many applied refactorings; 0 or
                less runs to a fixed point.
            progress: How progress is shown: ``tqdm`` (a bar when tqdm is
                installed), ``auto`` (that, or an inline bar), ``detail``
                (a line per proposal), or ``none``.
            output_path: An optional new copy to refactor. The original project
                is checked before the output is created, and subsequent checks
                retain its configuration and unchanged consumers.

        Returns:
            The final source, the number of refactorings applied, and their
            descriptions in application order.
        """
        self._change_log = []
        analysis_progress: ProgressMode = progress if wants_bar(progress) else "none"
        current_bytes = Path(file_path).read_bytes()
        try:
            current_code = decode_source(current_bytes)
            ast.parse(current_code, filename=file_path)
        except UnicodeDecodeError as error:
            raise ValueError(f"{file_path}: {error}") from error
        except SyntaxError as error:
            raise ValueError(f"{file_path}: line {error.lineno}: {error.msg}") from error
        self.begin_refactoring_run([file_path])
        if output_path is not None and Path(output_path).resolve() != Path(file_path).resolve():
            source, destination = Path(file_path), Path(output_path)
            copy_project(source, destination)
            if self._type_run_oracle is not None:
                self._type_run_oracle = relocate_oracle(self._type_run_oracle, source, destination)
            self._output_origin = (source, destination)
            file_path = output_path
            self._analysis_paths = (file_path,)
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

            if not proposals:
                # Fixed point reached - no more refactorings found
                break

            applied_one = False
            for proposal in proposals:
                if proposal in rejected:
                    continue
                rendered = self._rendered_or_none(file_path, proposal)
                if rendered is None:
                    rejected.add(proposal)
                    continue
                if rendered == current_code:
                    continue
                new_code = rendered
                apply_changes(
                    ChangePlan.from_sources({file_path: current_bytes}, {file_path: new_code})
                )
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
        return current_code, num_applied, descriptions

    def _rendered_or_none(self, file_path: str, proposal: RefactoringProposal) -> Optional[str]:
        """The file with ``proposal`` applied, or None when rendering it fails.

        A proposal the materializer or the compiler rejects is a defect in
        the rendering of that one extraction; it is dropped with a warning
        and the run goes on, rather than aborting after whatever was applied
        before it.
        """
        try:
            new_code = self.apply_refactoring(file_path, proposal)
            compile(new_code, file_path, "exec")
        except (RefactoringError, SyntaxError, ValueError) as error:
            self._report_dropped(proposal, error)
            return None
        return new_code

    @staticmethod
    def _report_dropped(proposal: RefactoringProposal, error: BaseException) -> None:
        LOG.warning(
            "Dropped a proposal that could not be rendered (%s): %s", proposal.description, error
        )
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
            output_dir: The directory to write into (the same as ``input_dir``
                to refactor in place).
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
        self.begin_refactoring_run(self._find_python_files(input_dir))
        if resolved_input != resolved_output:
            copy_project(input_path, output_path, allow_empty=True)
            if self._type_run_oracle is not None:
                self._type_run_oracle = relocate_oracle(
                    self._type_run_oracle, input_path, output_path
                )
            self._output_origin = (input_path, output_path)
            self._analysis_paths = tuple(self._find_python_files(output_dir))

        self._warn_about_frame_sensitive_files(output_dir)

        run = _DirectoryRun()
        termination_reason: TerminationReason = "fixed_point"

        try:
            return self._apply_until_fixed_point(
                output_path, run, reporter, max_iterations, termination_reason
            )
        finally:
            # A display thread must not outlive the run, however it ended.
            reporter.close()
            if run.applied:
                self.confirm_run_with_a_cold_checker(self._find_python_files(str(output_path)))

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
                proposal_queue = self._apply_and_refresh(proposal, proposal_queue, run, reporter)
            except (RefactoringError, SyntaxError) as error:
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
    ) -> Optional[List[RefactoringProposal]]:
        """Analyze the whole directory and queue its non-overlapping proposals; None at the fixed point.

        After the first pass, re-pair rewritten files and proposals deferred
        by rendering or checking. A changed project can make a deferred
        proposal valid, but an unchanged project cannot justify another pass.
        This pass is where a rejected proposal is heard again; until it, the
        localized re-analysis that follows each application finds the same
        proposal and is not allowed to retry it.
        """
        if run.global_revision == run.revision:
            return None
        run.rejected.clear()
        reporter.announce_analysis(output_path)
        restrict = (
            frozenset(run.changed_since_global | run.deferred_paths)
            if self.incremental_global_passes and passes_so_far > 0
            else None
        )
        proposals = self.analyze_directory(
            str(output_path),
            recursive=True,
            progress=reporter.analysis_mode,
            changed_files=restrict,
        )
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
    ) -> List[RefactoringProposal]:
        """Apply ``proposal``, invalidate what it touched, and refresh ``queue``.

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
        modified_files = self.apply_refactoring_multi_file(proposal)
        changed_paths = list(modified_files.keys())
        apply_changes(ChangePlan.from_sources(before, modified_files))
        for fpath in modified_files:
            run.record(fpath, proposal.description)
        if not changed_paths:
            return queue
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
    def _identity(proposal: RefactoringProposal) -> str:
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
        self._identities.add(self._identity(proposal))

    def __contains__(self, proposal: RefactoringProposal) -> bool:
        return self._identity(proposal) in self._identities

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
    revision: int = 0
    global_revision: Optional[int] = None
    applied: int = 0

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
        self._doing, self._counts = f"#{iteration}: {desc}", (applied, queued)
        bar = self._active_bar()
        if bar is None:
            self.inline(applied, queued, "apply", self._doing)
            return
        self._redraw()

    def _redraw(self) -> None:
        """Show what is happening now; called by the heartbeat as well as by the run."""
        applied, queued = self._counts
        with self._drawing:
            bar = self._active_bar()
            if bar is None:
                if self._doing:
                    self.inline(applied, queued, "apply", self._doing)
                return
            short = self._doing if len(self._doing) <= 44 else self._doing[:41] + "..."
            quietly(lambda: bar.set_postfix({"A": applied, "Q": queued, "on": short}, refresh=True))

    def applied(self, applied: int, queued: int, iteration: int, desc: str) -> None:
        """The ``iteration``-th proposal was applied."""
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
        """Stop the heartbeat; safe to call more than once and after a failure."""
        self._heartbeat.stop()

    def finish_at_fixed_point(self) -> None:
        """No proposals remain: close the bar, or end the inline bar's line."""
        self._heartbeat.stop()
        bar = self._active_bar()
        if bar is not None:

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
            bar.close()
        elif wants_bar(self._mode) and not self._use_tqdm:
            print(file=sys.stderr)
