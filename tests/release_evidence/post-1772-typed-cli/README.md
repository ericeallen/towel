# Complete typed CLI comparison, October 2, 2026

`measurements.json` records the observed inputs, installed wheel/runtime hashes,
commands' mode settings, every timing sample, output hashes and means. The
candidate runtime is commit `000d751`; its two performance changes are
inference-local subtype verdict reuse and immutable per-statement narrowing
facts. The published arm uses the actual code-towel 1.772 wheel.

All runs execute `towel dry <input>/packaging <output> --no-interactive
--progress detail`. The serial/default modes also pass `--no-format`;
serial sets `TOWEL_WORKERS=1`, while default and ordinary leave worker selection
automatic. Input is a controlled 24-file Packaging 26.3 fixture with strict mypy
configuration, not the entire upstream checkout or an ecosystem corpus.

Every candidate output Python file matches the preceding candidate's output
under the same settings. Published 1.772 and the candidate perform different
numbers of valid extractions, recorded per run. Logical subtype requests and
answers are compared separately, with each owned staging directory normalized
to the same project-relative file paths.

These are fresh-process, end-to-end timings on one native macOS machine with
ordinary Spotlight background activity. They establish this workload's result,
not a universal speedup or a release audit. Full logs and drivers are retained
in the shared Towel handoff directory
`20261002T130652Z-codex-typed-cli-performance`.
