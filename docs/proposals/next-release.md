# Work leading to 1.792

The owner authorized this scope on September 27, 2026. Work stays on
`codex/post-1772-performance`; publication remains the owner's action. The implementation and development
validation are complete; the owner selected 1.792 on September 30. Final
versioned validation follows [the release procedure](../RELEASING.md).
Sections below preserve intermediate checkpoints under their recorded dates;
the completed [validation](next-validation-final.md) and
[published-wheel comparison](published-1772-comparison.md) supersede their
pending-work statements.

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

## Initial measured proposal failures (September 27)

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

## Instrumentation checkpoint

The forward flow recognizes typeguard and numba applications and project
source/AST recompilation through ordinary calls and executed construction
hooks. It follows local callee and argument aliases, namespace iteration,
returns and comprehensions. Hook dispatch respects overrides and both forms
of `super`. The specific source argument matters: using method source only
as a diagnostic filename is not a body transformation. Namespace observation
and transforming an unrelated function remain eligible.

One independent review's counterexamples are permanent documented tests.
Its runtime checks preserve the original and inline compiler traces with
extraction refused, and permit one extraction in an overridden-hook control
with identical output. General dynamic metaprograms remain outside the
supported model. Full final-source release validation still follows the
remaining implementation work below.

## Typed performance comparisons

Frozen baseline `35035a7` and candidate `1b97196` were compared on September
27. The candidate includes precise signature construction, the erased-None
predicate guard, immutable definition indexing and Pyright reveal batching.
These are controlled experiments, not the final release corpus.
Both source commits are intermediate post-1.772 snapshots; later edits are
outside these timings.

Packaging 26.3 used the actual CLI through a fixed point, strict mypy 2.3.1,
Python 3.13.7 on macOS arm64, one worker, same-module extraction and no
formatter. The mypy configuration targeted Python 3.10. Four runs were
sequential in baseline / candidate / candidate / baseline order, after the
Sphinx profile finished. Imported source paths,
complete outputs and every signature attempt are recorded. Output Python
files are byte-identical within each arm.

| Per run | Baseline | Candidate |
| --- | ---: | ---: |
| Elapsed seconds, two runs | 34.18, 34.57 | 28.81, 28.95 |
| Applied extractions | 19 | 18 |
| Signature attempts | 27 | 18 |
| Rejected signatures | 8 | 0 |
| Failed materializations | 0 | 0 |

Mean time fell from 34.37 to 28.88 seconds, about 16% in this small sample.
The one fewer extraction is intentional: `_matches_literal`/`contains`
previously erased the connection between a None predicate and its subject,
then passed only after changing that parameter to Any. The guard now stays
with its caller. Correlated signatures and the comparison return signature
pass first time. One distinct checker-coverage refusal remains, attempted
twice in each run. Each run completed its final cold type check; no packaging
runtime suite was included in this timing experiment.

The Sphinx comparison used its actual production CLI startup with both
strict checkers, stopping immediately after the first Pyright reveal. Both
frozen wheels ran sequentially in the same Docker Linux environment:
CPython 3.12.14, Sphinx 9.1.1, mypy 1.19.1 and Pyright 1.1.407. Both asked
exactly 2,949 distinct requests across 218 files, containing 7,391 expression
probes, and returned the same 7,387 types with no failed-file reports. Four
unanswered expression probes were identical in both runs; they are not
checker failures. Per-file exchanges took 980.72 seconds; one project batch
took 8.65 seconds. This single comparison measures initial reveal time, not
the full fixed point. Final project validation and consumer coverage are
unchanged.
The run enabled cross-module extraction, disabled formatting and network
access, and used one Towel worker.

Durable scripts, frozen sources/wheels, inputs, output hashes, request/answer
comparisons and logs are under
`~/Knowledge/handoffs/towel/20260927T172821Z-codex-typed-performance/`.

## Capability recovery checkpoint

List-field expression values, match cases and except-star handler/finally
suites now reach existing safety analysis. Nonlocal and class-private guards
use only the moving block; local-variable annotation names no longer count
as runtime module reads. Positive extraction tests accompany controls for
real nonlocal cells, private-name mangling, evaluated annotations, store and
delete targets, pattern headers, exception subgroup cleanup and caller loop
control. The focused combined battery passes 137 tests. Broader nested-block
liveness remains separate: a captured name reassigned inside a case and used
later still receives the existing escape refusal.

## Validation before release

### Instrumentation indexing measurement

The production packaging scan repeated 36,741 lexical identity questions
over 558 distinct definitions. A module-tree index removes the repeated
walks; cached maps and tuples are immutable and their lifetime follows the
AST. Resolving imports or deciding whether a hook transforms a body is not
part of this memo. A comparison against frozen commit `35035a7` produced
identical complete application indexes, including unresolved names, with
558 identity calls. Evidence and the comparison script are retained in
`~/Knowledge/handoffs/towel/20260927T172821Z-codex-typed-performance/`.

### Preview and helper-quality checkpoint

The implementation sequence is complete. Default preview shares dry's
executor, flags, checker and formatter behavior, fixed-point passes, caps and
final cold validation. It shows the complete final diff and helper inventory.
Explicit `--quick` remains a partial structural listing. Tests compare actual
output bytes and inventories and verify input immutability and failure cleanup.

Helper construction drops free names captured only inside substituted
caller-side expressions and avoids orphan parameters when differing attribute
names force a whole lookup to be parameterized. A disposable scope-analysis
copy keeps mutable generated ASTs out of the source-analysis memos.

Equivalent plain module helpers introduced during this staged run can be
reused with their original signatures and host; user-defined names, including
helper-shaped names already in the input, retain independent behavior. Three
modules now share one real helper. The four- and eight-module regressions also
share one real helper: equivalent module helpers generated in independent
batches become aliases of the helper in the safe home, so their existing calls
retain their bindings without forwarding layers. The alias requires identical
plain positional signatures and bodies, inert annotations, generated provenance,
and a verified earlier import of the exact helper. Same-name collisions,
same-module consolidation and general consumer redirection remain deferred.
Only string and `None` annotations are admitted for this alias case; ordinary
inferred signatures such as `n: int` retain separate definitions. The typed
alias regression therefore starts from an explicit private stage with quoted
signatures and checks materialization, all consumers, and runtime behavior.

Completed focused checks include 137 CLI/capability/runtime-equivalence tests,
40 latest preview/input-construction tests, 121 reuse/capability integration
tests, and the independent implementer's 143 reuse/type/import cases. These
sets overlap and their counts must not be summed. Full candidate validation
remains separate below.

### Validation progress and remaining work

The fifth independent audit is complete. Its findings and the subsequent
star-import resolution defect are fixed. Completed non-corpus checks for an
earlier frozen source are recorded in [next-validation.md](next-validation.md);
those historical results do not establish validation of the current branch.

A later full corpus run on `acb176a` found that generated Tornado annotations
performed qualified attribute lookups while their module was still importing.
Commit `fb52d90` keeps generated compound annotations wholly quoted, including
unions and subscriptions that can invoke user-defined operations. Precise
checker types and existing source annotations are preserved. Regression tests
record both the import-order defect and overloaded-operation counterexamples;
[the design decision](../DECISIONS.md#generated-annotations-must-not-add-evaluation-september-27-2026)
explains why this is part of runtime preservation. Focused tests and quality
checks pass. The installed production CLI at `a2e6734` subsequently changed
35 Tornado files and preserved the exact before/after outcomes: 748 passed,
474 pre-existing failures and 186 skipped in the private Linux environment.
The generated output differs from the failed run only in helper annotation
quotation, with precise types and all other AST nodes unchanged. This closes
the observed runtime defect; it does not make Tornado's upstream suite green
or establish completion of the final full corpus.

The corrected frozen source `3af8c4b` now has a completed full corpus, serial
Linux interpreter matrix, transformed native suite, fuzzing and artifact checks.
The [completed validation report](next-validation-final.md) retains their exact
counts, platforms, resources, elapsed times and exceptions. It also preserves
the interrupted predecessor and the completed nonzero Towel corpus baseline;
those do not become green upstream suites. Later documentary regressions are
checked separately from the frozen suite counts.

The repository-owned [release gate](../RELEASING.md#repository-owned-evidence-gate)
requires exact prebuilt artifacts and reviewed evidence. The development
artifacts still numbered 1.772 remain validation only. The completed
[comparison against the published 1.772 wheel](published-1772-comparison.md)
shows a large first-reveal gain for Sphinx and no meaningful whole-command
speedup for Packaging. Final documentary amendments and rebuilt artifacts use
separate source-bound checks; they do not relabel the frozen runtime results.
The owner selected 1.792. Publication requires validation of that frozen
versioned release, exact-commit CI and the owner's action.
