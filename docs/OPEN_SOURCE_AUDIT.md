# Open-source readiness — 1.1.0a1

> Historical record of the September 12, 2026 alpha audit. The production-readiness pass that followed, with its counterexamples, repairs, and consumer evidence, is recorded in [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md), which supersedes the disposition below.

**Disposition: experimental alpha candidate; local verification complete.** The five follow-ups from the first audit have been implemented. The adversarial review found additional semantic and filesystem defects, repaired them, and added executable regressions. Publication has not occurred.

The earlier audit checkpoint is local commit `6247333390a4ffb049f1959d1c4b29e36fee6b39`, based on `3bf35f2`. This work continues on `audit/open-source-2026-09-12` in the isolated audit checkout. The original checkout's user changes and index are preserved. No push, remote tag, visibility change, or package upload was performed.

## Measurement environment

Every time, memory and disk figure in this document was measured on an Apple
M5 Max with 18 cores and 128 GiB of memory, writing to an APFS internal
volume. Unless a figure says otherwise it was taken with the machine
otherwise idle and with Towel's CLI defaults, and a figure that names a
commit was taken at it.

Where a figure concerns a project Towel was checking, the checker is that
project's own rather than Towel's: the Sphinx measurements run mypy 1.19.1
and pyright 1.1.407 from Sphinx 9.1.1 at `e44a40e`, and the mypy costs they
describe belong to that version.

## Five follow-ups delivered

1. **Recoverable application.** Immutable byte plans stage all originals and replacements, validate syntax and stale inputs, atomically replace individual files, and roll back caught failures. `towel recover JOURNAL` handles interrupted batches and refuses detected conflicts. Rename mappings form one complete batch. Out-of-place copying is staged before publishing the destination. Exclusive write access is required; a batch is not globally atomic to readers and successive extractions commit separately.
2. **Consumer projects' own tests.** Humanize and More-itertools are tested from disposable upstream copies before and after actual transformations. The same full suites qualify the comparison. Monte Chess's focused core tests helped discover a method bug but do not substitute for a full second consumer suite.
3. **Immutable planning and bounded ownership.** Proposal materialization and call construction no longer mutate caller-owned ASTs. Generated proposals include source digests; `plan_refactoring` produces frozen byte changes. Engines own bounded analysis sessions, with private graph snapshots, content invalidation, and isolation tests.
4. **Indexed generation and visible errors.** Blocks/signatures are shared across pairing and clustering. Exact buckets preserve the ordered candidate set. Unexpected compiler errors propagate; expected unsupported extraction is distinct. The pairing benchmark improves 3.82–10.75× on its fixtures. A measured regression in the new rebinding guard was removed by computing a frozen summary once per analyzer: the same profiled fixture fell from 97.446s to 6.153s with identical proposals/decisions. Large modules can still be expensive.
5. **Public surface and release preparation.** Unsafe legacy normalizers preserve compatibility with deprecation warnings and explicit semantic warnings. Wrapper calls copy their input ASTs. Documentation describes the real APIs, limits, security status, and release history. The prepared unused alpha version is `1.1.0a1`; Python 3.11 is the minimum. Local version tooling accepts prereleases and refreshes the lock/environment.

## Adversarial results

See [the adversarial report](ADVERSARIAL_REVIEW.md) for concrete failures and repairs. These include private-name mangling, comprehension scope, captured/rebound names, loop transfers, method receiver arguments, Hatch/setuptools import paths, lexical renaming across modules and Python versions, stale proposals, partial writes, interruption recovery, and CRLF input. Unsupported declared build configurations and ambiguous renames fail visibly.

The additional 30-case deterministic semantic battery compares every accepted proposal and repeated transformations. It is complemented by real consumer tests, not treated as a substitute for them. The general equivalence harness still samples values and has documented limits for complex class construction, imports, returned callables, and external effects.

## Final gates

- Python 3.11.14, 3.12.12 and 3.13.12: 1,016 passed and 34 subtests passed on each, with 88% coverage against the unconditional 85% gate.
- Black, Flake8, strict mypy, Bandit and all pre-commit hooks passed. The 42 test warnings are expected legacy-normalizer deprecations.
- All 26 golden outputs are unchanged; the independent snapshot review checked 220 changed callables. The 30-case hostile battery also passed after the rebinding optimization.
- Both complete consumer before/after pairs passed; exact commits, commands and skips are below.
- Fresh wheel/source inspection and isolated installed-CLI execution passed, including cross-module lexical renaming. Twine accepted both archives. Metadata requires Python >=3.11 and declares no runtime dependencies.
- All 41 external name/version pairs in the final lock are covered by the clean dependency audit, with no skipped entries. Selected credential-pattern screening covered 100 reachable commits and 1,006 historical blobs before this checkpoint; the staged candidate scan found no selected patterns. These are scoped checks, not proof of confidentiality.
- The original checkout's tracked diff hash remains `10b928325ebfb0dd3f0d164e5883764e06462074e2c6bd60dd06267d0631b32b`; staged-file status is unchanged.

The first concurrent matrix exposed shared temporary-directory contention in the stability test; the test now allocates a private directory. The changed test passes on 3.11 and 3.13, and the full 3.12 rerun uses the corrected test. No golden expectation was weakened. Python 3.10's null-byte parser difference is outside the newly approved minimum version.

## Consumer evidence

| Consumer | Full suite before | Full suite after | Real change |
|---|---|---|---|
| Humanize `3201e702ed7eae506f793fad0aec204f387aeb4c` | 798 passed, no skips | 798 passed, no skips | One cross-file extraction, two functions in two files |
| More-itertools `9ed3dbb0ae527230cd156d91d0af305478558fba` | 910 passed, five intentional doctest skips | 910 passed, same five skips | One clustered extraction used by four functions in `recipes.py` |

All four full-suite processes exited zero on Python 3.13.7. Humanize's own translation build ran before both copies were tested, enabling all locale tests. The identical full command was used before/after each project; tests were neither selected nor edited. More-itertools was refactored in single-file mode because Flit cross-file layout inference is explicitly unsupported. Its complete suite still ran. Source/resource hashes preserve the originals and verify identical frozen Towel code in both comparisons. No-change runs and the partial Monte Chess suite are excluded from these qualifying results.

## Assessment by dimension

| Dimension | Status | Assessment |
|---|---|---|
| Correctness | Minor issues within the tested subset; significant language limits | Reproduced failures repaired. Arbitrary dynamic reflection, external rebinding/effects, and all Python metaprogramming are not proven safe. Preview, review, and run consumer tests. |
| Architecture | Minor issues | Frozen application plans and owned caches isolate important boundaries; the engine/unifier remain large. |
| Code quality | Minor issues | Semantic failures no longer disappear into broad catches. Optional progress-display fallbacks remain. Further decomposition is useful but not a publication prerequisite. |
| Typing | Minor issues | Strict runtime typing passes; existing dynamic AST boundaries still use casts and some `Any`. |
| Tests/oracle | Minor issues with explicit limits | Fault injection, independent reproducers, ordered candidate comparison, own-consumer suites, and full interpreter matrix. Sampling is not proof. |
| Robustness | Minor issues | Recoverable per-batch writes, stale checks and durable journals; exclusive writer access and ordinary POSIX files required. |
| Security | Minor issues | Static/dependency/credential screening has a defined scope; trusted source is required for behavioral execution. Confidential reporting channel remains a maintainer publication decision. |
| Dependencies | Clean for the audited lock | All locked registry name/version pairs are audited; build/host environments and future advisories are separate concerns. |
| Performance | Significant limitation for large modules | Indexed generation is faster and exact; expensive unification remains, with a large-module consumer example documented. |
| Documentation | Clean for candidate scope | Experimental status, supported/tested versions, recovery procedure, rename boundaries and release history are explicit. |
| Packaging | Candidate verified locally | Typed marker, license, metadata, complete source archive and fresh installed CLI checks. |
| License/history | Minor issues | Existing Apache-2.0 declaration preserved; selected history screened. Historical local notes remain and are not proof of contribution provenance. |
| Publication | Awaiting external action | Artifacts can be reviewed locally. GitHub is private; historical PyPI versions are yanked. No push/upload/visibility action is authorized by this preparation. |

## Publication decisions and remaining limitations

The existing PyPI versions `1.0.0`–`1.0.4` are all yanked for broken import handling, and their version numbers cannot be reused. `1.1.0a1` was absent when checked; recheck before upload. The current repository is private. See [release preparation](RELEASING.md) for the verified history, artifact review, visibility decision, and confidential reporting/support-policy decisions. No contact address, response-time promise, or provenance clearance has been invented.

The standing instruction forbids this agent from pushing. The maintainer must publish the chosen local commit, approve the exact artifact hashes and destination, and perform or explicitly authorize the remaining publication operations through an allowed workflow. Hosted Linux CI has not been run by a local test matrix; it must run against the published commit before its result can be claimed.

Optional future engineering work includes a smaller engine decomposition, more complete Python binding/control-flow modeling, broader consumer corpora, and scalable unification scheduling. These are limitations to state, not a claim that every possible improvement is a release blocker. This candidate must remain experimental.
