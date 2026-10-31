# October 6 optimization measurements

All three comparisons execute the complete default installed CLI on fresh copies
of the same Packaging 26.3 fixture, with typing, formatting, automatic workers
and final checking. Each uses ABBA order, two samples per arm. All arms produce
19 refactorings across 8files with byte-identical output within each comparison.

| Optimization | Control mean seconds | Enabled mean seconds |
| --- | ---: | ---: |
| Immutable ownership memo |49.798|49.907|
| Witnessed Any-result pruning |47.495|48.591|
| Exact completed-refusal retention |47.303|47.375|

No complete-command speedup is established. Two observations per arm with
ordinary changing background activity do not establish a regression either.
The first two use source `e0c185675e094eb14db305eaadd1a918934eaf55`, the third
`8626d50e359a6aabfe19e34514bcb8b29ef768e2`. These are successive bounded
optimization comparisons, not a new comparison with published 1.772.

The `*-freeze.json` manifests bind commit, wheel SHA256, installed runtime and
dependency versions. `*-results.json` preserves raw observations and input/output
hashes; summaries preserve pairing and means. Gzip control patches decompress to
exact bytes whose SHA256 matches the corresponding freeze.
`measurement-source.py.txt` is the measurement harness.

`rehearing-request-counts.json` is instrumented work evidence, not timing. Both
variants execute 185 mypy exchanges and 30 materializations. The enabled context and
exact keys were available; the five definitive refusals were stored at revisions
5,6,7,12,18, then correctly invalidated after revision19. There was no applicable
unchanged-revision refusal to skip in this workload. Raw traces/CLI logs and
telemetry remain in the October 6 shared handoff.

The matrix and corpus were not rerun; these measurements do not clear release.

The earlier phase-only ownership experiment is retained in
`ownership-cost-*.json.gz`, `ownership-cost-source.py.txt`, and the two
`*-phase-freeze.json` manifests. It compares `3fdce9d` and `77f6793`, before
the later frame-transfer repairs, replaying 4,705 calls over 941 fixture-derived
inputs. All four result arrays match. Its roughly 93% CPU saving (0.279 to 0.020
seconds) is a bounded repeated-call diagnostic, not a complete CLI timing.
