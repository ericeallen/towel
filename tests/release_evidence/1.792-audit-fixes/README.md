# Repaired 1.792 observations, October 5, 2026

[Documentation index](../../../docs/README.md) ·
[Readiness](../../../docs/PRODUCTION_READINESS.md#1792-release-preparation) ·
[Performance](../../../docs/PERFORMANCE.md#october-5-repaired-candidate-comparison)

This is a documentation evidence subset, not the complete release-verification
bundle, a successful `just release` record or permission to publish. Original
observations retain source commit
`00b4f4f27eeb45a17236fc7af4e74a3881339b23` and runtime tree
`2b1181b59a50599f339b9dc552b383836e441c3d`, unchanged since the `c5311b4`
audit fixes. Later documentation edits do not relabel these observations.

`provenance.json` lists every retained raw record's original byte length and
SHA-256. Files are gzip copies without content edits; `/` in a record name
becomes `--` in its archive filename. The archive includes:

- all 12 full-CLI timing observations, their commands, exit statuses, input
  and output hashes, per-run CLI logs and original attempt/completion records;
- preflight installed-runtime file hashes and dependency versions;
- the capacity assessment and explicit external experiment changes;
- the original source/archive identities, fresh 141-project corpus summary,
  reviewed dispositions and failed-test identities;
- independent refusal integrity and corpus completion/prerequisite records;
- the preceding preparation verification summary, with its own source scope.

The experiment executed the full default typed/formatted CLI on fresh copies
of the controlled Packaging 26.3 fixture. Four fresh-process samples per arm
ran in two balanced blocks, each published / earlier control / repaired /
repaired / earlier control / published. Background thresholds were
observations rather than automatic rejection. `performance--attempt.json.gz`
retains the numeric thresholds from the driver; `CAPACITY-EXPERIMENT.json.gz`
records that changed policy. No quiet-host preflight pass is claimed.

The current candidate averaged 43.425 seconds versus 38.802 for published
1.772, 11.91% slower. The earlier `000d751` runtime averaged 35.375 seconds.
All commands exited zero without an untyped fallback; Python outputs match
within each arm and differ between arms. Current 1.792 performs 20
refactorings versus 18 for 1.772. These observations do not establish
identical-work throughput, the cause of the slowdown, sub-percent precision
or a whole-corpus result.

The corpus summary contains 96 PASS, 35 NO_CHANGE, five BROKEN_KNOWN reflection
differences and five CRASH-labeled import refusals. The raw driver exits 1
because of these non-PASS verdicts. Review, manifest expectations and unchanged
refusal worktrees establish their dispositions; exit 1 is not rewritten as
exit 0. Earlier records that say timing or refusal integrity is pending keep
their timestamps; the later completed assessment and integrity record
supersede those statements.

Full matrix/native/fuzz/self logs, corpus per-project raw phase logs, benchmark
inputs/outputs/drivers and five-second background-load samples remain in the
external shared handoffs. This subset cannot independently rerun or certify
every release gate. Complete observations live under
`~/Knowledge/handoffs/towel/20261005T125608Z-codex-1792-fresh-corpus-9ca3e1/`,
including timing attempt `performance-attempts/20261005T230650311609Z-88675`.
