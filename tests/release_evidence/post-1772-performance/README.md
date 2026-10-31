# Retained post-1.772 typed performance evidence

These records describe the completed September 27 comparison of baseline
`35035a747e446368b6a5c4f3b1cee3fd1b5487c3` and candidate
`1b9719612ae9e844df8fbab2775c0646a0ab7ebf`. Both are intermediate development
commits. They are not a comparison of the published 1.772 release with a
later release candidate, a new benchmark, or final release validation.

The source archive is
`~/Knowledge/handoffs/towel/20260927T172821Z-codex-typed-performance/`.
`provenance.json` maps every retained gzip file to its original relative
path, uncompressed size and SHA-256. Decompression yields the **unaltered raw
bytes**, including missing terminal newlines. Gzip containers use `mtime=0`;
they keep text-fixing hooks from changing the historical records. Never
normalize or edit the decompressed records to make a test pass.

The original operator README supplies platform, tool-version, worker,
Docker/network and sequencing details that the event logs do not record.
These are operator-recorded provenance, not facts inferred from the machine
running pytest. The packaging configuration independently records strict
mypy and a Python **3.10 checking target**; its interpreter was **3.13.7**.
The Sphinx input records independently identify its interpreter and tools.
The recorded distribution metadata still says `code-towel 1.772`; that
unchanged version string does not identify either measured source revision.

Packaging's four event streams independently count signature attempts,
rejections, materializations and repeated coverage refusals. CLI logs record
applications and fixed-point termination. `boundary.json` contains the
in-process timer used in the table; `packaging-comparison.log` separately
records outer process duration, exit status and sequential run order. Do not
substitute the outer duration or the earlier profiled `packaging-before` run.
The retained measurement script documents the production flags and timing
boundary. It is identical in all four run directories; their input
`pyproject.toml` files are also identical. No runtime suite was timed.

The frozen drivers corroborate the final cold-check claim: a successful
directory run with applications calls `confirm_run_with_a_cold_checker`
before returning. This is code-path evidence plus successful-run logs, not a
separate cold-check event (the recorder did not emit one). Every file under
each archived `snapshot-*/src/towel` was compared with its named commit's Git
blob before retention; `source_identity` records the count and digest of the
sorted path-to-SHA-256 map. Retained driver hashes and imported paths connect
the small subset to those snapshots. The raw `source.json` field named
`annotation_wiring_sha256` actually hashes **materialize.py**. Its original
mislabel is preserved; the tests check it against that file's recorded hash.

Sphinx spans and CLI logs independently record 218 per-file exchanges versus
one batch covering the same 218 generated diagnostic sources. The phase
timer in each raw `summary.json` includes reveal bookkeeping as well as
those exchanges; it is neither the sum of exchange durations nor total CLI
time. The retained profile scripts deliberately raise after the first
reveal, so these successful captures never run the full extraction loop.

The large original request dumps (76,572,412 bytes per arm) and answer dumps
(775,231 bytes per arm) stay in the source archive. Their raw SHA-256 digests
match exactly across arms. `omitted_sphinx_dumps` was independently reduced
from those raw JSON files during retention: request-list length; unique
requests after sorted-key JSON serialization; distinct `file_path` count;
sum of expression-list lengths; and sets of `(file_path, line, expression
index)` keys. Answer keys use the **index**, not the expression's text.
Subtracting answered keys leaves the four retained missing keys, with no
unexpected answers and no failed-file reports. This small digest/count
record corroborates the original recorder's aggregates; offline tests cannot
reconstruct an omitted raw dump or independently rehash it. The same limit
applies to `packaging_output_sha256`, independently recomputed from all 22
output Python files per run and checked against the raw comparison record.

The regression tests are offline documentary checks, not timings of the
current checkout. Raw evidence integrity is pinned independently of the
documentation assertions, and mutation tests exercise plausible false
claims. Changed runtime behavior is never grounds for changing these
historical expectations. New comparisons need new evidence and source
identities; corrections need corroboration and an explanation. Keep the
existing 1.772 release-evidence test and its distinct scope.
