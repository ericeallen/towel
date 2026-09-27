# Work after 1.772

The owner authorized this scope on September 27, 2026. Work stays on
`codex/post-1772-performance`; publication remains the owner's action.

## Preservation contract

Recognized body-transforming instrumentation receives the same protection
whether applied by a decorator, an ordinary call, or a class hook. This is
explicit support for selected metaprogramming, not a claim that decorators
cannot use reflection. Observations of new helper names, lookups, frames and
source layout remain excluded. The mere presence of a metaclass or lookup
hook is not grounds for refusing extraction. Existing interfaces, including
Protocol requirements, remain protected. Arbitrary dynamic instrumentation
is not generally resolved.

## Implementation sequence

1. Complete and test consistent instrumentation recognition. The existing
   class-host relaxation and Protocol alias analysis are already committed.
2. Account for actual failed proposals and improve their construction. Count
   candidate declines, distinct emitted proposals, signature attempts and
   final outcomes separately. The objective is to construct valid typed
   extractions, not merely to move expensive validation before a counter.
3. Profile Pyright reveals by file, repeated input and project revision.
   Memoize repeated pure work; address distinct expensive requests through
   batching or another measured algorithmic improvement. Preserve checker
   coverage and exact proposal discovery.
4. Recover list-field expression unification and enumeration inside match
   cases and except-star handlers; then the documented narrow guard defects
   involving unrelated nonlocal declarations, private names elsewhere in a
   method, and annotation-only names.
5. Route preview through dry's complete pipeline. Keep --quick explicitly a
   partial one-pass listing, not an upper bound on fixed-point discovery.
6. Remove redundant forwarding and unused parameters where doing so preserves
   evaluation and effects.

The owner clarified item 2 during implementation: proposals should almost
always be valid when emitted. Precise signature construction must inform
extraction, rather than relying on repeated rejected annotation variants.
Caller-side narrowing is a boundary constraint: retain the guard and any
assignment establishing a refinement used by remaining caller code. Find
smaller valid windows before maximal-block pairing. The goal is elapsed-time
improvement with type checking enabled, not a lower proposal counter; measure
the cost of early analysis against avoided checking. These decisions and
their regression coverage are recorded in `docs/DECISIONS.md` (September 27).

## Current measured proposal failures

A controlled source-only run on installed packaging 26.3, Python 3.13,
strict mypy, same-file extraction and no formatter reached a fixed point on
September 27. It used branch 4d314aa (the new instrumentation module was not
connected to the engine). This is not the release corpus or an estimate of
its overall rejection rate.

- 18 extractions applied.
- Three distinct proposals lost narrowing of Version._key_cache after the
  extracted call. Each was materialized and rejected twice as the project
  evolved: six failed materializations, not six independent defects.
- One distinct proposal could not be verified because of checker coverage;
  its two attempts stopped before materialization.
- 32 signature attempts: 18 succeeded and 14 failed. Six failures were the
  narrowing cases above; eight were signature-search retries for four
  proposals that eventually succeeded.

Examples of the successful proposals' rejected signatures include union
types that lost argument/result correlations in dependency_groups.py and
_parser.py, an overly broad Version-or-None parameter in ranges.py, and Any
returns in _ranges.py. These require inspecting inference and variant
selection, not broadening accepted checker errors.

The current architecture creates RefactoringProposal before completing and
validating its annotations. Some structural impossibility checks already
exist in _untypeable_as_proposed but run only during materialization. Final
project validation must remain a backstop while construction improves.

Transient evidence: /private/tmp/towel-next-proposals-a1cov6e_ contains the
input, output, measurement wrapper, event stream and complete run log.

### Final caller-narrowing measurement with strict mypy

A controlled four-run comparison on September 27 used packaging 26.3 source,
Python 3.13.7, mypy 2.3.1, same-file extraction and no formatter. Runs were
sequential in baseline / early / early / baseline order. The baseline disabled
only the new boundary filter; all other working-tree code was identical.
This measured the final filter after review corrections, after the test
processes finished. Imported source paths and filter hashes are recorded.

| Per run | Late rejection | Early boundary filter |
| --- | ---: | ---: |
| Elapsed seconds, two runs | 38.39, 39.91 | 35.60, 36.26 |
| Applied extractions | 18 | 19 |
| Signature attempts | 32 | 27 |
| Failed materializations | 6 | 0 |
| Early analysis cost, seconds | — | 1.14, 1.15 |

The eight annotation-search failures among ultimately successful proposals
remain, as does one distinct unverifiable proposal. The filter also recovers
an accepted `__eq__`/`__ne__` extraction. Mean elapsed time fell from 39.15 to
35.93 seconds, about 8% in this small sample. This is evidence of a modest
improvement, not a full-corpus estimate. Successful grouping and helper
numbering differ between arms; Python output is identical within each arm.
Every run completed the production path's final cold type check. No packaging
runtime test suite was run for this measurement.

Durable inputs, outputs, event streams, wrappers and logs are in
`~/Knowledge/handoffs/towel/20260927T151251Z-codex-narrowing-final-evidence/`.
The earlier prototype's measurement remains separately archived under
`20260927T144523Z-codex-narrowing-evidence/`.

## Caller-narrowing checkpoint validation

The final implementation passes 93 focused tests plus two property subtests,
and the declared `just check` gates: Black, Flake8, strict mypy and
Bandit. One bounded independent review confirmed its four boundary findings
resolved against the original examples. Test intent is recorded beside each
regression and protected by the contributor rule in `CONTRIBUTING.md`.

A broader run started before those final corrections: 7,828 passed, four
skipped, 24 subtests passed, and 19 failed. Sixteen wheel/import failures were
caused by omitting a writable `UV_CACHE_DIR`; one watchdog failure was the
sandbox denying its required `ps` observation. The other two were cache tests
whose old fixture relied on discovery emitting the now-filtered invalid
proposal. Their validation/cache assertions were preserved, with fixture
construction separated from discovery. All 19 failures passed on targeted
reruns: 59 import tests, one audited-wheel test, six ladder tests (included
in the 93 above), and three watchdog tests. No final-source full-suite,
coverage, interpreter-matrix or release-corpus completion is claimed.

## Validation before release

Run the full interpreter matrix, full corpus, self-dogfooding, documentation
and artifact checks, and the deferred fifth audit under the existing stop
rule. Mechanical checks run as processes. Use at most one focused independent
reviewer for semantic changes; revisit review only for changed code or an
unresolved finding. No completed final validation is claimed yet.
