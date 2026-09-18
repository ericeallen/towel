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
import time

from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple
from .models import RefactoringProposal
from .overlap import filter_overlapping_proposals
from .progress import (
    DEFAULT_PROGRESS,
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
        self, file_path: str, max_iterations: int = 10
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
        current_bytes = Path(file_path).read_bytes()
        current_code = current_bytes.decode("utf-8")
        num_applied = 0
        descriptions = []

        iteration = 0
        while True:
            # Analyze for refactoring opportunities
            # Important: invalidate cached analysis for this path so we see latest edits
            proposals = self.analyze_files([file_path], invalidate_paths=[file_path])

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
            current_bytes = new_code.encode("utf-8")
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
                markers = frame_sensitivity_markers(Path(path).read_text(encoding="utf-8"))
            except OSError:
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
        max_iterations: int = 10,
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
        import textwrap

        progress_mode, tqdm_wrapper, use_tqdm = self._resolve_progress_backend(progress)

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

        # Timing / ETA state (for heuristic ETA when total unknown)
        per_proposal_durations: List[float] = []

        # Progress helpers -------------------------------------------------
        progress_bar = None

        # Fallback inline bar (only when not using tqdm and not in detail/none)
        def _fallback_bar(applied: int, queued: int, phase: str, desc: str) -> None:
            # Suppress inline fallback bar when tqdm is selected or active, or in 'none'/'detail' modes
            if progress_mode != "auto" or use_tqdm:
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

        def _update_progress_postfix(applied: int, queued: int) -> None:
            """Keep tqdm postfix updates consistent."""
            if not (use_tqdm and progress_bar is not None):
                return
            bar = progress_bar
            quietly(lambda: bar.set_postfix({"A": applied, "Q": queued}, refresh=True))

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
                    list(changed_paths), invalidate_paths=list(changed_paths)
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
                        _detail(f"Localized +{len(localized)} follow-up(s)")
                        _fallback_bar(
                            total_applied,
                            len(queue),
                            "localized",
                            f"+{len(localized)} follow-ups",
                        )

            return queue

        # Note: We defer tqdm progress bar creation until we have proposals to apply.
        # This avoids an early line like "analyzing: 0it" with unknown totals.

        def _detail(msg: str) -> None:
            if progress_mode == "detail":
                LOG.info("[towel] %s", msg)

        # Main loop -------------------------------------------------------
        while True:
            if not proposal_queue:
                # Global analysis pass
                if progress_mode != "none":
                    try:
                        file_count = sum(1 for _ in output_path.rglob("*.py"))
                        _detail(f"Analyzing {file_count} file(s)...")
                    except OSError:
                        # Only the directory walk for a progress message; a real
                        # I/O problem will resurface in the analysis that follows.
                        pass
                # Show pairing progress during global analysis if user requested progress bars.
                analysis_progress_flag = progress_mode if wants_bar(progress_mode) else "none"
                restrict = (
                    frozenset(changed_since_global)
                    if self.incremental_global_passes and global_passes > 0 and changed_since_global
                    else None
                )
                proposals = self.analyze_directory(
                    str(output_path),
                    recursive=True,
                    verbose=False,
                    progress=analysis_progress_flag,
                    changed_files=restrict,
                )
                global_passes += 1
                changed_since_global.clear()
                if not proposals:
                    # Fixed point reached
                    if use_tqdm and progress_bar is not None:
                        # Ensure a clean newline so the last line doesn't meld with following prints
                        bar = progress_bar

                        def finish() -> None:
                            bar.refresh()
                            bar.close()

                        quietly(finish)
                    else:
                        if wants_bar(progress_mode) and not use_tqdm:
                            print()  # finish inline bar line
                    break
                proposal_queue = filter_overlapping_proposals(proposals)
                _detail(f"Discovered {len(proposal_queue)} proposal(s)")
                if progress_mode == "detail":
                    for i, p in enumerate(proposal_queue[:25], 1):  # cap verbose listing
                        short = textwrap.shorten(p.description, width=100, placeholder="...")
                        LOG.info("    %2d. %s", i, short)
                    if len(proposal_queue) > 25:
                        LOG.info("    ... %d more", len(proposal_queue) - 25)
                _fallback_bar(total_applied, len(proposal_queue), "discovered", "proposals queued")
                if use_tqdm and progress_bar is None:
                    # Lazily create tqdm now that we have proposals to apply
                    assert tqdm_wrapper is not None
                    try:
                        total_known = max_iterations > 0
                        # Use leave=False so subsequent prints don't duplicate the bar line.
                        progress_bar = tqdm_wrapper(
                            total=max_iterations if total_known else None,
                            desc="apply",
                            unit="it",
                            dynamic_ncols=True,
                            leave=False,
                        )
                        queued_ct = len(proposal_queue)
                        _update_progress_postfix(0, queued_ct)
                    except Exception:
                        progress_bar = None

            if not proposal_queue:
                break

            proposal = self._pop_next_proposal(proposal_queue)
            if proposal is None:
                break
            iter_start = time.time()
            last_desc = proposal.description
            # Suppress separate applying log line when tqdm active to avoid duplicate lines
            if not (use_tqdm and progress_bar is not None):
                _fallback_bar(
                    total_applied, len(proposal_queue), "apply", f"#{iterations+1}: {last_desc}"
                )

            # Apply proposal. A proposal computed before an earlier application
            # changed one of its files is stale: drop it and re-analyze those
            # files so a fresh proposal can take its place.
            try:
                proposal_queue = _apply_proposal_and_refresh_queue(proposal, proposal_queue)
            except ChangeConflict as conflict:
                stale_paths = sorted({path for path, _ in proposal.source_digests})
                _detail(
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
                refreshed = self.analyze_files(stale_paths, invalidate_paths=stale_paths)
                if refreshed:
                    proposal_queue = filter_overlapping_proposals(refreshed) + proposal_queue
                continue

            # Record duration for this iteration (include localized follow-up analysis time)
            per_proposal_durations.append(time.time() - iter_start)
            iterations += 1
            total_applied += 1
            if use_tqdm and progress_bar is not None:
                bar = progress_bar

                def advance() -> None:
                    bar.update(1)
                    _update_progress_postfix(total_applied, len(proposal_queue))

                quietly(advance)
            else:
                _fallback_bar(
                    total_applied, len(proposal_queue), "applied", f"#{iterations}: {last_desc}"
                )

            if max_iterations > 0 and iterations >= max_iterations:
                termination_reason = "iteration_cap"
                if use_tqdm and progress_bar is not None:
                    progress_bar.close()
                else:
                    if wants_bar(progress_mode) and not use_tqdm:
                        print()
                break

        return results, termination_reason
