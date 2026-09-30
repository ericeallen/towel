# Completed corrected-source validation after 1.772

The records below validate commit `3af8c4bfe8acd56c204f5e31c7cf80f4f028b654`, runtime tree
`78c8c6ea954f370a1afa8846a41988856589acc5`, and test tree `63c2bb4b75e17dbe12947d83dc9219346eecd6af`.
Artifacts still carry version 1.772 and must not be uploaded. Later documentation
and evidence tests are separate from these frozen suite counts. The
[earlier validation](next-validation.md) keeps its original source identity.

The [retained evidence](../../tests/release_evidence/post-1772-corrected-validation/README.md)
contains raw command outputs and hash inventories. The external archive is
`~/Knowledge/handoffs/towel/20260927T181305Z-codex-next-validation/release-gates-3af8c4b`.
Documentation regressions compare the statements here with those immutable
records; a later result cannot silently replace a historical expectation.

## Environments and limits

Native host: macOS 26.5.1 arm64, Apple M5 Max, 18 cores, 128 GiB
(137438953472 bytes). Docker VM: 18 CPUs and 8215732224 bytes (about 7.65 GiB).
The container environment was Linux arm64, kernel 6.10.14-linuxkit, glibc 2.41.
No Windows or Linux x86_64 validation is established here. These are observed
environments, not minimum resource requirements. Validation elapsed times are
not controlled performance comparisons.

## Linux interpreter matrix

The jobs ran sequentially in 3.11, 3.13, 3.12 order, with container networking
disabled. Each imported Towel from its own frozen checkout, ran the full suite
under coverage, combined subprocess coverage, and checked the frozen source
afterward. Coverage is line coverage; the gate was 85%.

| CPython | Passed | Skipped | Subtests | Pytest seconds | Job wall seconds | Covered / executable lines | Line coverage |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 3.11.16 | 7,877 | 76 | 24 | 2827.28 | 2,871 | 27,110 / 28,952 | 93.64% |
| 3.12.14 | 8,009 | 15 | 24 | 3140.27 | 3,177 | 27,194 / 28,952 | 93.93% |
| 3.13.15 | 8,022 | 5 | 24 | 2783.21 | 2,819 | 27,189 / 28,952 | 93.91% |

Pytest seconds and whole-job wall seconds have different boundaries. The latter
include environment setup and coverage reporting; start timestamps have
one-second resolution. The retained driver uses `UV_LINK_MODE=hardlink`, bytecode,
and the frozen coverage configuration for threads and multiprocessing workers;
inherited pytest, import-path and coverage overrides are cleared.

## Native transformed-source suite and fuzzing

Self-refactoring applied 31 extractions across 24 files. The complete transformed
project passed its configured strict mypy check and full native suite on
CPython 3.13.7. All 97 transformed runtime files remained unchanged during
testing. The generated tree and imported source were independently checked.

Result: 8,096 passed, 3 skipped, 24 subtests in 1936.44 pytest seconds.
Test subprocess: 1966.500 seconds. This is a transformed native
suite, separate from the original-source Linux matrix.

Both differential fuzz families used four workers and completed with zero
failures. Unchanged and inconclusive fuzz cases are not demonstrated
transformed equivalence; finite sampling does not prove general equivalence.

| Family | Runs | Equivalent | Unchanged | Inconclusive | Harness seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| grammar | 7,400 | 4,638 | 2,725 | 37 | 417 |
| scope | 2,000 | 1,968 | 32 | 0 | 52 |

## Complete corpus

141 projects; driver elapsed 17,706.2 seconds.
The default typed harness ran one project at a time with `TOWEL_WORKERS=1`,
container networking disabled, and the exact candidate wheel installed in each
project's environment. It recorded 594 changed files.

| Raw verdict | Projects |
| --- | ---: |
| BROKEN_KNOWN | 1 |
| CRASH | 5 |
| NO_CHANGE | 37 |
| PASS | 98 |

Known refusals remain CRASH: `invoke`, `isort`, `towel-main`, `towel-v1.414`, `towel-v1.618`.
Their complete baselines and safe import-origin refusals are retained; they
do not establish successful extractions.

Untyped fallbacks: `cheroot`, `trio`, `typing_extensions`.
Untyped fallbacks do not establish typed extraction coverage. Trio's original
check fails on an installed Sphinx dependency's syntax; typing_extensions'
direct source check conflicts with its module name; Cheroot's project hook
works with its Python 3.12 target, while Towel's combined invocation retains a
different default target. That Cheroot integration gap remains open. These
observations do not establish a defect in upstream CI.

Pyparsing's BROKEN_KNOWN result is limited to the manifest-listed traceback
reflection tests. Matching pre-existing test failures do not make an upstream
suite green. The review retains every nonzero baseline, before/after failing
test identities, typed fallback and known limitation. Checker-untypable and
unreachable regions also remain excluded where each raw run records them.

The earlier two-project run suffered an out-of-memory kill. Its Towel baseline
exited -9 before extraction. Observed cgroup lifetime peak: 7385477120 bytes.
This is not a baseline-only peak or a minimum memory requirement. The corrected
one-project run completed that baseline without an OOM kill. The old killed
run remains retained and is not counted as successful validation.

Towel-main baseline: 18 failed, 7771 passed, 38 skipped. Review of all 18
tracebacks identified 16 offline fixture-build dependency failures and two
historical annotation-spelling assertions. This is a completed nonzero baseline,
not a green Towel suite. The candidate deliberately quotes precise compound
annotations to keep their runtime evaluation inert. The current-source matrix
and transformed native suite separately test that policy. The original queue
stopped on the nonzero baseline; its stop record remains alongside the reviewed
continuation. No extraction was applied to this corpus project.

## What the declined-proposal counts mean

The console counts distinct built proposals not applied, each once even when
reconsidered. Candidate-pair refusals belong to the last whole analysis and
are a separate count. Neither is a count of checker invocations or all
construction attempts, so these logs cannot establish a repeated-work rate.

The two large typed projects illustrate the remaining reasons:

| Reason recorded by the production CLI | Sphinx | Tornado |
| --- | ---: | ---: |
| not verifiable: its file holds a name the type checker cannot type | 44 | 1 |
| narrows what a call-site lambda reads | 16 | 4 |
| completes its caller's partial type | 6 | 1 |
| refused by the type checker | 6 | 2 |
| declares its class's attributes | 3 | 1 |
| not verifiable: the type checker does not look at the code it changes | 3 | 1 |
| narrows what its caller reads after the call | 1 | 0 |
| Total distinct built proposals not applied | 79 | 10 |

Sphinx applied 318 extractions and Tornado applied 160. In Sphinx, 47 of the
79 declined proposals lack checker coverage; 26 preserve caller narrowing,
partial-type completion or class declarations; six were refused by the checker.
Precise helper annotations address only part of this population. These are
final-run observations, not measurements of how much time each guard costs.
The ordinary console does not preserve every refused candidate's diagnostic,
so it does not establish the precise cause of those six checker refusals.

## Artifacts and ancillary checks

The original wheel and source archive were compared against the frozen source;
wheel RECORD hashes and full distribution contents were checked. Strict Twine
checks passed for both. These local validation artifacts retain their original
identities:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `code_towel-1.772-py3-none-any.whl` | 798,763 | `36e0c954c11d1217c60aa6479993ad458fc2e384d860d1369aa6ea11cbd3d60a` |
| `code_towel-1.772.tar.gz` | 2,635,994 | `c6161d2824bdba61c2f9ebf7c9449d864032ed6d3afa689c2e5484b3dd91f74f` |

Six fresh wheel environments passed: bare and format/types extras on CPython
3.11.14, 3.12.12 and 3.13.7. Each checked its installed import path, preview and
interactive-decline immutability, actual extraction and program output parity.
The extras fixture configured strict mypy, not both installed checkers; its
final cold check ran through the installed CLI. Bare installs exercised the
explicit unverified-typing fallback.

The native quality command passed. The retained dependency pins had no known
vulnerabilities at audit time. Diagram validation reported 5 diagrams, 0 broken.
The source archive's documentation regressions passed from a fresh environment.
These are the checks of the frozen archives; later documentary amendments and
their rebuilt source archive receive separately recorded checks.
