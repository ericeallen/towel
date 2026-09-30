# Frozen validation of the post-1.772 candidate

[Proposals and experiments](README.md) · [Documentation index](../README.md)

The completed non-corpus gates below validate commit
`3f09421f2139508c5c9549b6791eca43f30c5c41`, with runtime tree
`747008c225808e48bfaf97455e1d808e8800aa59` and test tree
`829ef595f3249c06d92685ff702ed536e6b6c3a2`. They are local validation of an
unreleased candidate, not results for the published 1.772 release. The local
artifacts still carry version 1.772 and must not be uploaded.

Documentation and evidence tests added after this freeze are separate checks;
they are not included in the suite counts below. These records establish the
source that ran, not a claim that a later HEAD received the same full gates.
The earlier typed-performance comparisons also use their own intermediate
commits, as documented in [next-release.md](next-release.md).

Raw records, original paths, and SHA-256 digests are retained in
[the validation evidence](../../tests/release_evidence/post-1772-validation/README.md).
The offline regressions derive suite counts, coverage, fuzz outcomes, and smoke
results from those records. They do not rerun the gates or query the current
machine. The full external archive is
`~/Knowledge/handoffs/towel/20260927T181305Z-codex-next-validation/final-gates`.

## Recorded environments and measurement limits

At the time of these runs, the native host was macOS 26.5.1 arm64, Apple M5 Max,
18 cores, with 128 GiB of memory (137438953472 bytes). The Docker VM had
18 CPUs and 8215732224 bytes of memory. The Linux matrix ran on Linux arm64
(aarch64), kernel 6.10.14-linuxkit with glibc 2.41; the native transformed-source
suite and wheel smokes ran on macOS arm64. These are recorded conditions, not
claims about the machine currently running the documentation tests.

No Windows or Linux x86_64 validation is established by these records.
These measurements are not minimum resource requirements or timing guarantees.
Host memory and VM memory are different quantities; neither is a measured peak
memory requirement. The runs were validation jobs, not controlled performance
comparisons.

## Completed Linux matrix

The three jobs ran sequentially in 3.11 / 3.13 / 3.12 order with container
network access disabled. Each synchronized the frozen development dependencies,
verified its imported `towel` path inside its checkout, and ran
`coverage run -m pytest -q --durations=20`, followed by `coverage combine` and
`coverage report --fail-under=85`. `UV_LINK_MODE=hardlink` and bytecode were
on; inherited `PYTEST_ADDOPTS`, `PYTHONPATH`, and coverage overrides were unset.
The frozen coverage configuration traces threads and multiprocessing workers.
All three jobs exited 0 after the coverage report and a tracked-source diff check.

| CPython | Passed | Skipped | Subtests passed | Pytest seconds | Approx. job wall seconds | Covered / executable lines | Line coverage |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 3.11.16 | 7,808 | 76 | 24 | 2951.30 | 2,993 | 27,138 / 28,983 | 93.63% |
| 3.12.14 | 7,940 | 15 | 24 | 3314.83 | 3,353 | 27,230 / 28,983 | 93.95% |
| 3.13.15 | 7,953 | 5 | 24 | 2783.10 | 2,819 | 27,217 / 28,983 | 93.91% |

Pytest seconds come from pytest's completed summary. Approximate job wall
seconds come from the recorded shell start and finish timestamps and include
setup, shutdown, and coverage reporting; the start timestamps have one-second
resolution. Coverage is line coverage, not branch coverage. The console rounds
all three reports to 94%; each exceeded the 85% gate. Counts remain separate
because these are different interpreter/platform runs, including skipped cases.

Each matrix environment reported mypy 2.3.1, Pyright 1.1.414, pytest 9.1.1,
and coverage 7.16.0. Both checkers were available to the test suite; this does
not mean every test or every extraction ran both checkers.

## Completed native gates and transformed-source suite

On native CPython 3.13.7, `just check` completed: Black checked 448 files,
flake8 passed, and the configured strict mypy gate checked 437 source files.
The frozen mypy configuration targeted Python 3.11. Bandit's `-ll` gate
reported 0 medium and 0 high severity issues; it also recorded 62 low severity
issues. The dependency audit reported no known vulnerabilities for its retained
installed pins at that time, and the diagram checker reported 5 diagrams with
0 broken. These are scoped tool results, not a security guarantee.

Self-refactoring applied 31 refactorings across 24 files. Eight proposals were
not applied: 6 were refused by the type checker and 2 narrowed what a call-site
lambda reads. The log also records an unresolved/untyped `ruff` import and
2 checker-unreachable regions excluded from changes. This was not an extraction
of every discovered proposal or full checker coverage of every input region.

The resulting runtime files were installed into a complete checkout of the
frozen project. The imported module path was verified to be that transformed
checkout. Its configured mypy gate passed, followed by the full native test
suite: **8,027 passed, 3 skipped, and 24 subtests passed in 1829.53 pytest
seconds**. The test subprocess wall time was 1859.416 seconds; this is separate
from pytest's reported duration and excludes the preceding sync and mypy steps.
Post-test integrity checks covered all 97 runtime files, including `py.typed`,
and found the transformed bytes unchanged by the test run. The frozen candidate
remained unchanged. This is a transformed-source suite run, not an additional
Linux coverage run.

## Completed differential fuzzing

Both runs used 4 workers. Grammar seeds 1790541100..1790543099 produced
7,400 runs: 4,637 equivalent, 2,726 unchanged, and 37 inconclusive, with
0 failures; the harness reported 414 seconds. Scope seeds
1790543100..1790545099 produced 2,000 runs: 1,968 equivalent and 32 unchanged,
with 0 inconclusive and 0 failures; the harness reported 48 seconds.
Inconclusive and unchanged cases are not counted as demonstrated transformed
runtime equivalence. These finite samples do not prove general equivalence,
and the aggregate logs do not establish that every run was typed.

## Completed artifact and installed-wheel checks

The frozen wheel and sdist were checked against all 97 runtime files
(96 Python files plus `py.typed`). The wheel's 103 RECORD entries were verified.
The sdist inspection compared its tracked contents with the frozen commit,
allowing only setuptools' neutral `egg_info` addition to `setup.cfg`;
the distribution-content and metadata checks passed. Twine metadata checks
passed for both artifacts. These are validation artifacts, not upload candidates.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `code_towel-1.772-py3-none-any.whl` | 799,112 | `19635d07cc6d1211238d8ef43ab46f40263788e48ed898551e1b73e47b92af13` |
| `code_towel-1.772.tar.gz` | 2,254,265 | `7fba9dff0bcc7aec4e9576013b295303845fe3b1c15149f71fa87f52c073d81f` |

Six clean virtual-environment wheel smokes passed on native macOS arm64:
bare and `[format,types]` installs for CPython 3.11.14, 3.12.12, and 3.13.7.
Each verified the import came from that environment's `site-packages`, complete
preview left input unchanged, declining the interactive prompt wrote no output,
and noninteractive dry applied one extraction with identical program output.

Bare installs had no mypy, Pyright, or Black and exercised the explicit
unverified-typing fallback. Extras installs exercised configured strict mypy
and Black, annotated helper output, and a final cold-check reset observed
through the installed CLI. Both ordinary and cold-check output programs matched
the original. This smoke fixture configured mypy, not a dual-checker project;
installing Pyright does not establish that it checked this fixture.

## Corpus

Corpus validation is pending in this document. No corpus completion, totals,
or release-readiness conclusion is inferred from the non-corpus gates above.
