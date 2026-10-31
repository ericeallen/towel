# Frozen post-1.772 non-corpus validation evidence

These are completed records from
`/Users/ericeallen/Knowledge/handoffs/towel/20260927T181305Z-codex-next-validation/final-gates`,
for frozen commit `3f09421f2139508c5c9549b6791eca43f30c5c41`, runtime tree
`747008c225808e48bfaf97455e1d808e8800aa59`, and test tree
`829ef595f3249c06d92685ff702ed536e6b6c3a2`. No corpus results are retained here.
Documentation/evidence regressions added later were not part of these frozen
suite counts. None of these local artifacts should be uploaded: the unreleased
source still used the existing version 1.772.

Each gzip file decompresses to the original bytes, including whitespace and
missing final newlines. Compression uses `mtime=0`. `provenance.json` records
original relative paths, byte counts, and SHA-256 values. The regression test
pins that manifest's hash, checks the exact fixture inventory, and checks every
raw digest. Do not normalize, replace, or edit historical records merely to
make a test pass. A legitimate correction requires the original instrument
output and an explanation; a later run needs its own evidence and scope.

The retained subset contains:

- Frozen source/platform metadata, the frozen project configuration, completed
  matrix logs/results/launches, the driver scripts and completed driver log,
  and coverage XML. The tests independently count executed lines from XML and
  reconcile them with pytest and coverage summaries. Matrix wall times derive
  from the result timestamps; starts have one-second precision.
- Native quality, pinned dependency audit, diagram, and differential-fuzz logs.
  Fuzz outcome totals come from the final progress and completion records,
  including inconclusive cases. The logs do not prove every fuzz run was typed.
- Self-refactoring output, its before/after source hashes, full transformed
  suite/type-check/import logs, step results, post-test integrity record, and
  the whole-project setup driver. The pytest duration is distinct from the
  measured test subprocess duration. Original generation flags are not fully
  retained, so no unstated cross-module/formatting configuration is claimed.
- Artifact hash/content records and their inspection scripts, Twine output,
  and all six clean-wheel smoke command records. The smoke script and observed
  cold-reset records explain which behavior was asserted beyond exit status.

At retention time, every frozen runtime file was independently compared with
its Git blob; all 96 Python SHA-256 values matched `source.json`. The actual
wheel and sdist hashes matched `artifacts.json`; their 97 runtime files matched
frozen source bytes, and wheel RECORD hashes were verified. All 97 transformed
runtime files still matched both their recorded after hashes and the generated
files after the suite. Those archives and runtime trees remain in the external
archive, not this small fixture set. Offline tests can verify the retained
records' integrity and consistency, but cannot rehash omitted artifacts or
rerun omitted source. Inspection scripts are evidence and are never executed
by these tests.

Platform/resources in `source.json` were recorded for those runs, not queried
from the live machine by the tests. macOS arm64 and Linux arm64 are distinct;
137438953472 host-memory bytes (128 GiB) must not be confused with 8215732224
VM-memory bytes. There is no Windows or Linux x86_64 evidence, minimum-resource
claim, timing guarantee, or current vulnerability guarantee in these records.
