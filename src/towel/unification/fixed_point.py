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
queue drains re-pairs the project (only the files rewritten since the last
global pass, which is exact; see docs/ARCHITECTURE.md) until no proposal
remains or the iteration bound is reached. Before a directory run, modules
that inspect their own frames are named in a warning. Progress is shown
through tqdm when available, else an inline bar, else nothing.
"""

from __future__ import annotations

import os
import textwrap

from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple
from .defaults import DEFAULT_MAX_ITERATIONS
from .models import RefactoringProposal
from .overlap import filter_overlapping_proposals
from .progress import (
    DEFAULT_PROGRESS,
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
from towel.changes import ChangeConflict, ChangePlan, apply_changes
from ..diagnostics import LOG, REJECTIONS, debugging
from ..filesystem import copy_project
from ..source_text import decode_source, encode_like, read_source

from .engine_state import EngineState


class FixedPointDrivers(EngineState):
    """FixedPointDrivers methods of the engine; see the module docstring."""

    @staticmethod
    def _start_inline_status(label: str, enabled: bool) -> None:
        """Emit the leading inline progress label when progress is enabled."""

        if enabled:
            print(label, end=" ", flush=True)

    @classmethod
    def _update_inline_status(
        cls, label: str, pct: int, *, bar_len: int = 24, suffix: str = ""
    ) -> None:
        """Print an inline progress update with consistent formatting."""

        bar = render_inline_bar(pct, bar_len=bar_len)
        suffix_text = f" {suffix}" if suffix else ""
        print(f"\r{label} [{bar}] {pct:3d}%{suffix_text}", end="", flush=True)

    @staticmethod
    def _finish_inline_status(enabled: bool) -> None:
        """Terminate the inline status line so subsequent logs stay readable."""

        if enabled:
            print()

    @staticmethod
    def _pop_next_proposal(queue: List[RefactoringProposal]) -> Optional[RefactoringProposal]:
        """Remove and return the oldest queued proposal."""

        if not queue:
            return None
        return queue.pop(0)

    def _resolve_progress_backend(
        self, progress: ProgressMode
    ) -> Tuple[ProgressMode, Optional[ProgressBarFactory], bool]:
        """Resolve the progress mode and load tqdm if it is available."""

        normalized = normalize_progress(progress)
        use_tqdm = normalized in {"auto", "tqdm"}
        tqdm_cls: Optional[ProgressBarFactory] = None
        if use_tqdm:
            tqdm_cls = load_tqdm()
            use_tqdm = tqdm_cls is not None
        return normalized, tqdm_cls, use_tqdm

    def refactor_to_fixed_point(
        self,
        file_path: str,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        progress: ProgressMode = DEFAULT_PROGRESS,
    ) -> Tuple[str, int, List[str]]:
        """
            Apply refactorings iteratively until a fixed point is reached.

        This method applies refactorings one at a time, re-analyzing after each
        application. This prevents the sequential corruption bug where applying
        multiple refactorings at once causes line number misalignment.

        max_iterations semantics:
        - If max_iterations > 0, stop after at most that many applied refactorings.
        - If max_iterations <= 0, run until a natural fixed point (no proposals found).

            Args:
                file_path: Path to file to refactor
                max_iterations: Maximum iterations to prevent infinite loops

            Returns:
                Tuple of (final_code, num_refactorings_applied, descriptions)
        """
        self._change_log = []
        analysis_progress: ProgressMode = progress if wants_bar(progress) else "none"
        current_bytes = Path(file_path).read_bytes()
        current_code = decode_source(current_bytes)
        num_applied = 0
        descriptions = []

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

            # Apply only the first proposal
            proposal = proposals[0]
            new_code = self.apply_refactoring(file_path, proposal)
            # Idempotence guard: if no change, stop to avoid churn
            if new_code == current_code:
                break
            compile(new_code, file_path, "exec")
            apply_changes(
                ChangePlan.from_sources({file_path: current_bytes}, {file_path: new_code})
            )
            current_code = new_code
            current_bytes = encode_like(current_bytes, new_code)
            num_applied += 1
            descriptions.append(proposal.description)

            iteration += 1
            if max_iterations > 0 and iteration >= max_iterations:
                break

        return current_code, num_applied, descriptions

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
    ) -> Tuple[Dict[str, Tuple[int, List[str]]], str]:
        """
        Apply refactorings across a directory (recursively) until a fixed point.

        Unlike the per-file variant, this performs whole-project analysis on every
        iteration so it can apply BOTH same-file and cross-file proposals. One proposal
        is applied per iteration, then the directory is re-analyzed, up to max_iterations.

        Args:
            input_dir: Input directory path
            output_dir: Output directory path (results are written here)
            max_iterations: Maximum iterations to prevent infinite loops. If <= 0,
                run until a natural fixed point (no proposals remain).
            progress: Progress display mode: 'tqdm' for percentage bar if available,
                'auto' fallback to simple inline bar, 'none' disables progress output.

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

        if resolved_input != resolved_output:
            copy_project(input_path, output_path, allow_empty=True)
        elif not output_path.is_dir():
            raise ValueError("Input directory does not exist")

        self._warn_about_frame_sensitive_files(output_dir)

        # Aggregate results per file
        results: Dict[str, Tuple[int, List[str]]] = {}

        def _bump_result(path: str, desc: str) -> None:
            count, descs = results.get(path, (0, []))
            results[path] = (count + 1, descs + [desc])

        # Proposal processing state
        proposal_queue: List[RefactoringProposal] = []
        # Files rewritten since the last global pass: the next global pass
        # re-pairs only functions in these (see ``incremental_global_passes``).
        changed_since_global: Set[str] = set()
        global_passes = 0
        iterations = 0
        total_applied = 0
        termination_reason = "fixed_point"

        def _apply_proposal_and_refresh_queue(
            proposal: RefactoringProposal, queue: List[RefactoringProposal]
        ) -> List[RefactoringProposal]:
            """Apply a proposal, invalidate the affected caches, and refresh the queue."""
            before = {
                path: Path(path).read_bytes()
                for path in {
                    proposal.file_path,
                    *(rep.file_path or proposal.file_path for rep in proposal.replacements),
                }
            }
            result = self.apply_refactoring_multi_file(proposal)
            if isinstance(result, tuple):
                modified_files, changed_paths = result
            else:
                modified_files = result
                changed_paths = list(modified_files.keys())

            apply_changes(ChangePlan.from_sources(before, modified_files))
            for fpath in modified_files:
                _bump_result(fpath, proposal.description)
                changed_since_global.add(os.path.abspath(str(fpath)))

            if changed_paths:
                self.invalidate_paths(changed_paths)

            if changed_paths:
                changed_set = set(map(str, changed_paths))
                queue = [
                    p for p in queue if not ({path for path, _ in p.source_digests} & changed_set)
                ]

                localized = self.analyze_files(
                    list(changed_paths),
                    invalidate_paths=list(changed_paths),
                    progress=reporter.analysis_mode,
                )
                if localized:
                    localized = filter_overlapping_proposals(localized)
                    localized = [
                        p
                        for p in localized
                        if any(
                            (rep.file_path or p.file_path) in changed_set for rep in p.replacements
                        )
                    ]
                    if localized:
                        queue = localized + queue
                        reporter.detail(f"Localized +{len(localized)} follow-up(s)")
                        reporter.inline(
                            total_applied,
                            len(queue),
                            "localized",
                            f"+{len(localized)} follow-ups",
                        )

            return queue

        # Main loop -------------------------------------------------------
        while True:
            if not proposal_queue:
                # Global analysis pass
                reporter.announce_analysis(output_path)
                restrict = (
                    frozenset(changed_since_global)
                    if self.incremental_global_passes and global_passes > 0 and changed_since_global
                    else None
                )
                proposals = self.analyze_directory(
                    str(output_path),
                    recursive=True,
                    verbose=False,
                    progress=reporter.analysis_mode,
                    changed_files=restrict,
                )
                global_passes += 1
                changed_since_global.clear()
                if not proposals:
                    reporter.finish_at_fixed_point()
                    break
                proposal_queue = filter_overlapping_proposals(proposals)
                reporter.discovered(proposal_queue, total_applied, max_iterations)

            if not proposal_queue:
                break

            proposal = self._pop_next_proposal(proposal_queue)
            if proposal is None:
                break
            last_desc = proposal.description
            reporter.applying(total_applied, len(proposal_queue), iterations + 1, last_desc)

            # Apply proposal. A proposal computed before an earlier application
            # changed one of its files is stale: drop it and re-analyze those
            # files so a fresh proposal can take its place.
            try:
                proposal_queue = _apply_proposal_and_refresh_queue(proposal, proposal_queue)
            except ChangeConflict as conflict:
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
            total_applied += 1
            reporter.applied(total_applied, len(proposal_queue), iterations, last_desc)

            if max_iterations > 0 and iterations >= max_iterations:
                termination_reason = "iteration_cap"
                reporter.finish_at_cap()
                break

        return results, termination_reason


class _ApplyProgress:
    """How the directory driver reports progress: a tqdm bar, an inline bar, or detail lines.

    Which of the three applies is fixed by the progress mode and by whether
    tqdm loaded. The driver reports what happened and never asks which one
    is showing it. The tqdm bar is created only once there are proposals to
    apply, so a run never opens with an empty bar.
    """

    _LISTING_CAP = 25

    def __init__(
        self, mode: ProgressMode, factory: Optional[ProgressBarFactory], use_tqdm: bool
    ) -> None:
        self._mode = mode
        self._factory = factory
        self._use_tqdm = use_tqdm
        self._bar: Optional[ProgressBar] = None

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

    def inline(self, applied: int, queued: int, phase: str, desc: str) -> None:
        """Redraw the inline bar; only the automatic mode without tqdm shows one."""
        if self._mode != "auto" or self._use_tqdm:
            return
        denom = max(applied + queued, 1)
        pct = int((applied / denom) * 100)
        bar = render_inline_bar(pct, bar_len=32)
        short = desc if len(desc) <= 48 else desc[:45] + "..."
        quietly(
            lambda: print(
                f"\r[towel] {phase:<10} [{bar}] {pct:3d}% "
                f"| applied={applied} queued={queued} | {short}",
                end="",
                flush=True,
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
        if self._use_tqdm and self._bar is None:
            assert self._factory is not None
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

    def applying(self, applied: int, queued: int, iteration: int, desc: str) -> None:
        """About to apply the ``iteration``-th proposal; the tqdm bar already says so."""
        if self._active_bar() is None:
            self.inline(applied, queued, "apply", f"#{iteration}: {desc}")

    def applied(self, applied: int, queued: int, iteration: int, desc: str) -> None:
        """The ``iteration``-th proposal was applied."""
        bar = self._active_bar()
        if bar is None:
            self.inline(applied, queued, "applied", f"#{iteration}: {desc}")
            return

        def advance() -> None:
            bar.update(1)
            self._postfix(applied, queued)

        quietly(advance)

    def _postfix(self, applied: int, queued: int) -> None:
        bar = self._active_bar()
        if bar is not None:
            quietly(lambda: bar.set_postfix({"A": applied, "Q": queued}, refresh=True))

    def finish_at_fixed_point(self) -> None:
        """No proposals remain: close the bar, or end the inline bar's line."""
        bar = self._active_bar()
        if bar is not None:

            def finish() -> None:
                bar.refresh()
                bar.close()

            quietly(finish)
        elif wants_bar(self._mode) and not self._use_tqdm:
            print()

    def finish_at_cap(self) -> None:
        """The iteration cap was reached: close the bar, or end the inline bar's line."""
        bar = self._active_bar()
        if bar is not None:
            bar.close()
        elif wants_bar(self._mode) and not self._use_tqdm:
            print()
