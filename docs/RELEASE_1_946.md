# Towel 1.946: changes since 1.792

[Documentation index](README.md) · [Changelog](../CHANGELOG.md#1946)

**1.946 is prepared for publication; its upload is unverified.** The number
names ln 7, approximately 1.945910149. Version 1.792 is the last published
release recorded here.
These changes extend the existing extraction model and have no intended public
API or CLI incompatibility. The [version convention](RELEASING.md#version-number-convention)
records the limited remaining three-decimal labels below 2.0.

The main gains are more useful accepted extractions, cleaner generated calls,
and stronger checks of ordinary Python behavior and type contracts. Full-command
performance remains below the 1.792 baseline on the two newly measured workloads.

## More duplicate code can be shared

### Exact constant occurrences

Constants are matched by their position in the syntax tree, rather than by
their value alone. In a repeated computation containing `x + 1 + 2` and
`x + 2 + 2`, the first operand difference can become a parameter without
replacing the common final `2`. Repeated differences share a parameter only
when their complete typed values agree across every caller. This also handles
permuted constants, multiple callers, booleans versus integers, signed zero
and large integers under the existing interface budget.

Shared literals and f-string fragments are carried from their original callers
where necessary to preserve compiler-pooled object identity. Complete literal
expressions are passed when replacing individual parts would rebuild an object.
Unproved folded floating arithmetic and private single-byte literals keep their
original sites. The ordinary ownership, formatting-effect and parameter-budget
checks still apply. See [generated code](GENERATED_CODE.md) and the
[complete-CLI constant controls](../tests/test_occurrence_constant_cli.py).

### Fresh outputs from conditional blocks

Contiguous duplicate blocks inside supported `if` suites can now initialize
fresh local names that later statements read. The helper returns those values
to the original site. Supported cases include nested `if` ancestry, different
output names, and later continuations that use the returned values.

Acceptance requires an exact original-source and branch certificate, definite
initialization, and independent reproof of the aligned outputs. Values required
for lifetime preservation are returned in the original cleanup order too.
Earlier reads, incompatible rebinding, captured outputs, unsupported ancestry,
or missing source proof still prevent extraction. This is not general recovery
across loops, `try`, asynchronous control flow or nested definitions. See
[binding and ownership](ARCHITECTURE.md) and the
[branch-local CLI controls](../tests/test_if_fresh_bindings_cli.py).

### Fewer unnecessary lambdas

Eligible leading thunk parameters are ordered by the expression evaluation
sequence before eager inlining. A difference in parameter discovery order no
longer forces an otherwise eligible argument to remain a zero-argument lambda.
Pair and cluster reconstruction use the same ordering pass.

Only contiguous eligible positions within each positional signature segment
move. Other parameters and the positional-only boundary retain their positions;
defaulted, variadic and keyword-only signatures remain outside this pass.
Every caller must prove an immutable result as well as unchanged evaluation
order. Attributes, factories, unknown values and other unproved results stay
deferred when eager evaluation could change their lifetime or exception cleanup.
See [arguments and evaluation](GENERATED_CODE.md#arguments-and-evaluation) and
[ordering controls](../tests/test_thunk_ordering_cli.py).

## More precise typing and stronger correctness checks

- **Optional-aware expressions:** a read of a tested subject is distinguished
  from an operation that actually requires narrowing. Valid nullable results
  remain eligible, including supported named arguments and reflected comparisons.
  Ambiguous operands remain unresolved.
- **Caller type contracts:** declared returns, annotated assignments and fixed
  tuple components are checked before helper annotation fallback could widen
  away a required constraint. Correspondence handles supported early and repeated
  branch returns, nested assignments and unchanged aliases. An unannotated caller
  does not erase a return type already proved for another caller.
- **Deferred predicates:** extraction is refused when moving a test into a
  deferred predicate would lose a narrowing proof needed by the caller. A helper
  annotation fallback is not permission to discard that obligation.
- **Object lifetimes and literals:** eager thunk conversion requires an immutable
  result proof from every caller. Literal pools, shared f-string fragments,
  temporary release and exception cleanup retain their original observations.
- **Captured comprehensions:** targets sharing an outer free binding remain at
  their original sites when moving them could change compiler cell restoration
  on Python 3.12 or 3.13. Ordinary independently bound comprehensions stay eligible.
- **Import configuration:** malformed non-table Poetry metadata no longer aborts
  cross-module import analysis or discards valid PEP 621 restrictions.
- **Independent validation:** retained locals and readonly suppliers are certified
  by compiler scope and source correspondence, without exemptions based on
  generated identifier spelling. Skipped source layout and original import
  boundaries remain checked. Required scope fixtures are included in the sdist.

The [typing guide](TYPING.md), [limitations](KNOWN_LIMITATIONS.md) and
[adversarial review](ADVERSARIAL_REVIEW.md) describe the accepted cases and
conservative refusals. Reflection and self-instrumentation were already outside
the preservation guarantee in 1.792; this release does not introduce that policy.

## Statistics that explain losses

`towel dry --stats` (and the complete `preview --stats`) accounts for every
candidate pair of the run's last whole analysis: declined, a duplicate of an
earlier pair's proposal, dropped for overlapping a kept proposal, or emitted.
It also counts the run's helper signatures as verified or refused by the type
checker, and the proposals not applied. `RunReport` carries the same counts.
Before this, the report gave only declined pairs and unapplied proposals, so a
reader could not tell where a project's duplicates went. The flag only reports,
and output files are identical with and without it. See the
[CLI guide](CLI_GUIDE.md#diagnostics).

## Bounded reuse of analysis facts

Repeated analysis can reuse immutable whole-body ownership facts, IF binding and
flow facts, statement read summaries, compiler-literal facts and warning-free
lexical classifications. Caches have explicit source, parser and analysis-scope
boundaries. Mutable query results are returned fresh, source storage is bounded,
and analysis cleanup releases retained owners even when memoization is disabled.

Impossible branch transfers are rejected before expensive whole-module proof.
Within an owned analysis, shape and unification failures can also be discovered
before fresh source admission. Accepted sites still require current source and
AST identity checks; aligned-output and materialization proofs remain independent.
No checker-result cache replaces prospective or final project checks.

Source-cache keys accept only native strings and bytes, account for parser limits,
and retain fresh native diagnostics for warning-capable sources. Python 3.12 raw
f-string literal text can share a warning-free analysis while raw format
specifications and nested nonraw strings keep their native warning behavior.
Mapping item and value views tolerate reads that refresh LRU recency.

Ordinary raw-regex brace escapes before every possible formatted-string start
can also reuse the existing immutable module facts. A bounded production-path
replay reproduced repeated parsing of an unchanged, warning-free Voluptuous
test module; the guard refinement restores its cache eligibility while leaving
potentially warning-capable formatted strings on the fresh native path.

A subsequent guard repair distinguishes complete paired backslashes from an
unmatched odd escape before a formatted-string brace. Even runs do not themselves
escape that brace; all existing token, prefix, nesting and native warning checks
still apply. The measured Markup/ANSI-like source shapes can reuse immutable
facts when admitted, while uncertain inputs retain fresh native parsing. There
is no new cache or change to diagnostic filters, capacity or parser-limit keys.

The integrated seven-file focused battery passed 479 tests with 221
version-conditioned skips on Python 3.13. Independent external classifier/native
AST, error and warning comparisons covered 3,719 finite inputs on each native
Python 3.11–3.14. Permanent fixtures preserve always/ignore/error behavior,
repeated queries, source changes and disabled-cache recomputation. Neither the
finite controls nor construction counts establish a wall-time benefit or full
release acceptance. This later runtime repair is outside the earlier `a6dc6ed`
measurements and requires a new freeze and fresh final gates.

These are exact analysis changes, not a claim of overall acceleration. The
[architecture](ARCHITECTURE.md) records their boundaries; the
[performance guide](PERFORMANCE.md) records complete-command measurements.

## Completed development evidence and remaining release work

The [October 18 publication checkpoint](RELEASING.md#1946-publication-checkpoint-october-18-2026)
records the maintainer's request to publish without further tests. The
source includes `--stats` from `93b3b0a` and the documentation-only decisions
through `8a25ded`; source-correspondence memoization remains deferred. Remote
publication has not been verified. The development results and incomplete
campaigns below retain their original scope and do not claim a newly passed gate.

The final full development audit completed on `9d57b0d`, with metadata still
numbered 1.792: 10,583 tests and 45 subtests passed, 32 skipped, and coverage
was 93.478%. Strict typing and the declared static gates passed. Broad behavioral
assessment covered 9,400 cases: 4,501 equivalent, 4,842 unchanged, and 57 original
timeouts that remain inconclusive. Known scope-oracle limits remain uncertified.
Six consumer original-command pairs preserved their counts and visible failure
payloads; all 531 generated Python outputs matched their references. Existing
consumer failures and nine truncated diagnostic portions were retained as limits.

The earlier `9d57b0d` cold production comparison retained two samples per version
per workload in R,F,F,R order:

| Workload | Released 1.792 mean | Development mean | Additional wall time | Additional CPU time |
| --- | ---: | ---: | ---: | ---: |
| Click | 46.53 s | 56.69 s | 21.85% | 19.49% |
| Rich | 99.77 s | 177.70 s | 78.10% | 78.00% |

This is shared-host evidence, not isolated-host certification. All runtime
intervals were retained. Click applied four refactorings with 1.792 and five
with the development source; Rich applied 15 with each. Original before/after
tests and each version's expected generated outputs matched in all eight runs.
The [retained measurement record](../tests/release_evidence/1.946-preparation/README.md)
keeps the source identities, raw samples and limits.

The first versioned candidate, `a53e351`, completed quality, distribution,
clean-install, differential, interpreter-matrix and self-dogfood checks. Its
corpus campaign stopped after 31 of 141 projects when Voluptuous exposed a
material performance defect. That incomplete campaign does not approve a
release. The guard repair changes runtime bytes; earlier passing gates do not
certify the repair or replace its fresh complete validation.
The repaired versioned source `a6dc6ed` now has fresh exact artifacts/static
checks and a complete 182/182 Voluptuous canary. Eight actual R,F,F,R samples
across two complete cohorts compared it with published 1.792: Voluptuous means
were 90.984 → 87.569 seconds (−3.75%) and Click 47.165 → 58.195 seconds
(+23.39%). These are two shared-host observations per source per workload,
using 2 CPUs, 6 GiB RAM and 12 GiB combined RAM+swap. Original before/after
outcomes, all 24 displayed Click failure records and repeated outputs matched;
per-CLI CPU is unavailable. Rich has zero actual samples in this new campaign
and remains pending. The [current portable record](../tests/release_evidence/1.946-warning-guard/README.md)
keeps exact source/wheel identities and telemetry/closure limits.

The standard [release procedure](RELEASING.md) calls for final frozen artifacts,
the supported interpreter matrix, the 141-project corpus, the exact release-evidence
gate and exact-commit CI. The current publication instruction starts no further
tests; missing results remain unclaimed. Live remote and artifact verification
are still outstanding.

### Where the remaining time goes (profiled on `0b3494c`)

On 2026-10-18 the frozen candidate `0b3494c` was profiled against published
1.792 (`44d7795`) on the same pinned Rich and Click revisions. The runs used the
same command, one Towel worker, and environments built by the corpus harness.
The host was a shared native macOS machine with Python 3.12, not the release
container, so these are attribution and relative timings, not release
measurements. Interleaved R,F,F,R wall times:

| Workload | 1.792 samples | 1.946 samples | Difference | Applied (1.792 / 1.946) |
| --- | --- | --- | ---: | --- |
| Rich | 90.5 s, 90.7 s | 97.4 s, 97.6 s | +7.6% | 15 / 15 |
| Click | 41.4 s, 68.0 s | 60.6 s, 89.2 s | +37% (noisy) | 4 / 5 |

Host load changed during the Click series, so only Rich's difference is
precise. The earlier +78% on Rich was measured on `9d57b0d`, before the later
warning-guard and parity repairs.

- **Rich.** The whole difference is in pair evaluation. One proof explains it:
  the fresh whole-module source correspondence that admits each fresh output
  from an `if` block (`if_binding_transfer._source_corresponds`). It ran
  1,964 times, for 10.5 s unprofiled, which is more than the 6.9 s
  difference; the rest of 1.946's analysis is slightly faster than 1.792's.
  In 99% of those calls the inputs (module, source and tree) repeat an
  earlier call, so a per-analysis memo would remove nearly all of the cost.
  The [2026-10-10 decision](DECISIONS.md) requires a fresh proof for every
  admission and forbids reusing a verdict about the tree, so the proof is not
  cached. A byte-identical faster serializer saved only about a quarter of
  the dump time and was not adopted.
- **Click.** Most of the extra time pays for the fifth extraction: one more
  whole analysis, one more materialization, and almost twice as many
  type-checker runs (11 against 6). The same `if` proof adds about 3 s under
  the profiler.

Disposition (maintainer decision D1, 2026-10-18): the regression was
profiled. Its single hot spot cannot be removed without relaxing the
2026-10-10 proof rule, so 1.946 is to be published with the regression documented
here. This is a release decision, not confirmation of a completed upload.

## Deferred work

The four selected bounded proposals are implemented: ownership-fact reuse,
exact constant occurrences, certified thunk ordering and fresh IF-local outputs.
The remaining groups are:

- Broader control-flow recovery across loops, asynchronous code and nested definitions.
- Bare-name and mixed pure/impure thunk precision.
- General project-owned checker invocation and environment integration.
- Generation and shipping of shared helper modules.
- Richer receiver contracts, function-local generics and PEP 695 output.
- Exact N-way discovery or Rust work justified by fresh complete-path profiles.
- Template Method and agent-assisted readability research; automatic MRO edits remain ruled out.
- Unified JSON and stat preview formats; complete checked preview already exists.

Decorator trust overrides and redirection to existing user functions remain
withdrawn. The [proposal index](proposals/README.md) records individual ideas,
their proof obligations and their historical evidence.
