# Published 1.772 versus the next candidate

[Proposals and experiments](README.md) · [Documentation index](../README.md)

The baseline is the published PyPI wheel, not a rebuilt tag. Both artifacts
report version 1.772; their hashes distinguish them. These are measurements
of a development candidate; its artifact must not be uploaded as 1.772.

| Arm | Source commit | Wheel SHA-256 |
| --- | --- | --- |
| published | `088a4c7c7992aa1ff59d90cc88e083ccf157b82d` | `4af0f6067d830e396aecf82320d69cb82343a848f24b7d1f3e2a152018605683` |
| candidate | `3af8c4bfe8acd56c204f5e31c7cf80f4f028b654` | `36e0c954c11d1217c60aa6479993ad458fc2e384d860d1369aa6ea11cbd3d60a` |

Each experiment used the order published, candidate, candidate, published.
Fresh input for each run; one Towel worker. The benchmark begins after the
corpus and serial matrix have completed, and after the supplemental validation
checks. The retained timestamps and prerequisites document that ordering.
Two observations per version do not establish a statistical confidence
interval. No whole-corpus speedup is established.

Validation and Git packing had finished before timing. Ordinary desktop
activity continued: the three-second preflight observed Mail using about
64% of one CPU and the Docker VM process about 33% of one CPU on the 18-core
host. This was not a dedicated idle machine. Process snapshots and the
alternating order expose some interference and drift; two samples cannot
eliminate every background-load confound.

| Measurement | Published mean seconds | Candidate mean seconds | Less time |
| --- | ---: | ---: | ---: |
| Packaging | 39.977 | 39.771 | 0.51% |
| Sphinx reveal | 931.071 | 8.632 | 99.07% |

## Complete Packaging command

The Packaging samples show essentially unchanged elapsed time: a 0.51%
difference in means, with overlapping run times. This does not establish a
meaningful whole-command speedup over published 1.772. The candidate applied
19 extractions in nine files, versus 18 in eight files for the published
version, so the work performed also differs. The earlier approximately 16%
improvement compared intermediate commits; it is not the published-versus-final
result, and percentages from different baselines must not be added.

Packaging: whole unmodified CLI subprocess. The interval includes discovery,
type inference, proposal construction, checking, fixed-point extraction and
output writing. Environment setup and postchecks are outside it. The fixture
uses Packaging 26.3 with the same strict mypy configuration targeting Python
3.10, run on native CPython 3.13.7, macOS 26.5.1 arm64, M5 Max, 18 cores,
128 GiB. Both environments use identical dependency versions. Formatting and
cross-module extraction are disabled. This reproduces the earlier controlled
fixture; it is not a claim to run Packaging's entire upstream CI.

| Run | Arm | CLI seconds | Applied extractions | Changed files |
| --- | --- | ---: | ---: | ---: |
| 1 | published | 39.637 | 18 | 8 |
| 2 | candidate | 39.169 | 19 | 9 |
| 3 | candidate | 40.374 | 19 | 9 |
| 4 | published | 40.317 | 18 | 8 |

Input bytes remained unchanged. Output Python bytes matched across both runs
of each version. The comparison preserves any difference in extraction counts
between versions; a timing reduction alone does not demonstrate equal output
or equivalent work. Complete logs retain each version's refusal summaries.
Runtime correctness is covered by the separately recorded validation, not by
the timer itself.

## Initial Sphinx type reveal

Sphinx: first production Pyright reveal only. The same observer calls each
installed version's unmodified reveal implementation, records its complete
requests and answers, then stops the CLI before extraction. Serialization is
outside the reveal timer. Sphinx extraction and runtime tests are outside this
timing. The run uses the same original Sphinx project, configuration and
checker pins in one offline Linux arm64 container (CPython 3.12.14); its Docker
VM has 18 CPUs and about 7.65 GiB. These are observed resources, not minimum
requirements. The container completed without OOM.

2,862 requests across 218 files; 7,206 expression probes and 7,202 reported types.
Unanswered expression probes are not counted as inferred types. Identical
complete Sphinx requests and answers were verified across all four runs. The
retained request fingerprints preserve every field except source bodies,
which are represented by byte lengths and SHA-256 hashes. The raw archive
retains the original complete bodies and the retention script checks them
against those fingerprints.

| Run | Arm | Reveal seconds | Reported types |
| --- | --- | ---: | ---: |
| 1 | published | 924.163 | 7202 |
| 2 | candidate | 8.664 | 7202 |
| 3 | candidate | 8.600 | 7202 |
| 4 | published | 937.979 | 7202 |

## Evidence and interpretation

[Retained records](../../tests/release_evidence/post-1772-published-comparison/README.md)
include each command, imported runtime identity, wheel/source hashes, exact
dependency pins, request and answer hashes, timings, outputs and prerequisites.
The external raw archive is
`~/Knowledge/handoffs/towel/20260927T181305Z-codex-next-validation/performance-vs-1772`.
Documentary regressions reject changes in these historical facts and in their
scope. Intermediate optimization percentages are not added to this comparison.
