# Open-source readiness audit — 2026-09-12

**Disposition: experimental release candidate; local verification complete.** The original green suite did not justify a production-stable claim. Reproduced semantic and filesystem defects have been repaired, and release checks have been strengthened. Remaining design limitations below must remain visible to users.

## Scope and preservation

This audit covers correctness, architecture, code quality, typing, tests and their oracle, robustness, security, dependencies, performance, documentation, packaging, and release readiness. Work is isolated on `audit/open-source-2026-09-12`, based on `3bf35f2`. Pending import-layout repairs from the original checkout were copied into this worktree. The original checkout and index were preserved. No pushing, publication, remote release, history rewriting, or tagging was performed.

Eric's stated open-source approval is accepted. The Apache-2.0 declaration and copyright are preserved. This technical audit does not independently establish the provenance of every historical contribution.

## Principal findings and repairs

The baseline suite passed **797 tests in 659.02 seconds**, yet independent execution found the following defects. New tests exercise the failure modes instead of merely asserting generated text.

1. **Output safety.** Existing or overlapping destinations are rejected before copying. Cancellation leaves output untouched. Symlinked Python files are excluded. The old dry script delegates to the common CLI. See `src/towel/cli.py:267` and the directory API in `src/towel/unification/refactor_engine.py`.
2. **Python semantics.** Production analysis no longer rewrites ordinary assignment into augmented assignment or subtraction into addition. Generator/suspension and frame-sensitive extraction is rejected. Shadowed builtins retain lexical bindings. Nested extractions are rejected when local writes are used outside the block or on later loop iterations; the guard includes import aliases and pattern bindings. This repairs concrete undefined-name failures in structure-building and aggregation examples. Unaliased dotted imports correctly bind their first component. Static local import cycles, cross-module global declarations, and unsafe module-data snapshots are rejected. See `semantic_safety.py`, `scope_analyzer.py`, and `pipeline.py` under `src/towel/unification`.
3. **Helper validity.** Substitution keys preserve AST structure instead of unparsed text; Python 3.9 gives a formatted-value node and its enclosing f-string identical unparsed text, and previously allowed substitution of the wrong node. An executed regression now preserves string `"42"`. Helpers precede module-time calls; proposed files compile before application. Clustering now requires the same generalized helper and parameter order for every added caller. The old clustering changed `membership_a(0, {})` from `2` to an exception on the first extraction. Ten successive iterations now preserve its result. Class-context helpers use a single underscore to avoid Python name mangling, including inherited dispatch; collision detection and helper listing recognize both naming styles. See `refactor_engine.py:588` and `:2554`, plus `tests/test_semantic_safety_regressions.py`.
4. **Behavioral oracle.** Every replacement is selected from source-location metadata, including extra clustered callers. Direct methods are actually invoked with constructor inputs; returned values and instance state are checked after successful method returns. Cross-file executions isolate imports. Single-file executions use fresh globals but share interpreter imports. Both compare stdout/stderr, argument mutation, exception behavior, and scalar types. Empty selections, unsupported construction, and constructors that never reach the method cannot silently pass. See `tests/equivalence_targets.py`, `tests/automatic_equivalence_tester.py`, and `tests/crossfile_equivalence_tester.py`.
5. **Renaming and robustness.** Identifier-token edits preserve strings/comments, reject keywords and collisions, validate syntax before writing, and honor file-qualified JSON selections. Cached source is rechecked; malformed files produce diagnostics. See `src/towel/cli.py` and `src/towel/unification/pipeline.py`.
6. **Import layout.** Conventional setuptools projects with classic packages under `src/` now produce the installed module name even when `package-dir` is omitted. The original standalone diagnostic asserts `monte_chess.ai.mcts`; an independently built setuptools wheel confirms that name. Isolated-interpreter tests exercise imports. Explicit configurations, other backends, malformed metadata, and unconfigured directories retain existing behavior. This is conservative recognition, not complete emulation of every backend or namespace-package configuration.
7. **Release engineering.** Golden comparisons use AST structure and generated-identifier normalization, preserving literal contents and helper bindings while ignoring printer differences. Tests explicitly reject operator, call-target, and literal changes. Strict mypy covers all runtime modules. CI/local failures propagate; coverage is an unconditional 85% gate per interpreter. The universal lock includes development tools and a conditional TOML backport for Python 3.10. The maintainer approved raising the minimum to Python 3.10; CI and the lockfile exclude the legacy 3.9 toolchain. License metadata, package URLs, typed-package marker, archive contents, installation guidance, and experimental status are explicit. Just recipes use `.venv`; unsafe wildcard cleanup is replaced by a read-only `clean-preview`.

## Verification evidence

All four supported CI versions passed the final suite and unconditional 85% coverage gate locally:

| Python | Passed | Statement coverage |
|---|---|---|
| 3.10.20 | 888 + 36 subtests | 88% |
| 3.11.14 | 888 + 36 subtests | 88% |
| 3.12.12 | 888 + 36 subtests | 88% |
| 3.13.12 | 888 + 36 subtests | 88% |

Each run covered 4,660 of 5,317 statements (87.64%, displayed as 88%). All process exit codes were zero. These local macOS results do not claim that hosted Linux CI has run. Additional evidence:

- All **26** fixed-point example outputs compiled and passed sampled behavioral checks for **220 changed functions and methods on each of Python 3.9 and 3.13**. Only then were the **23 changed snapshots** accepted. All 26 final output ASTs also agree between those interpreters. This checks sampled behavior, not universal equivalence.
- Historical minimum-version investigation: managed Python 3.9.25 completed **881 tests with four syntax-version skips**. This is additional evidence, not a support promise: the approved final minimum is Python 3.10.
- All pre-commit hooks, Black, Flake8, and strict mypy pass; mypy checks **24 files**, including all runtime modules and two typed test helpers. A runtime union expression inside `cast()` was also corrected for Python 3.9; postponed annotations alone do not protect ordinary expressions.
- Bandit reports no medium/high findings. Its lower-severity findings remain visible; this is not a claim of zero scanner findings.
- The final advisory audit covers **all 45 registry package versions in the regenerated universal lock**, with zero findings and zero skips. The audited name/version set was checked against the lock manifest, including both conditional versions of the same package. Earlier environment scans covered 40 distributions on Python 3.13 and 42 on Python 3.10, also clean; the universal-lock check is the final complete dependency measurement. The legacy Python 3.9 finding below was resolved by the approved minimum-version change.
- Wheel/source archives and installation outside the repository pass checks: all **22 runtime Python files** match source bytes; the wheel contains **29 files**, including the typed marker and license; the source archive contains all fixtures/templates and excludes local state. A real installed CLI run preserves sampled results and input bytes. Metadata requires Python 3.10 or newer; the source archive has 273 members.
- A disposable copy of **31 committed Python files** from the chess project completed one directory iteration: all 31 outputs compile, one file was modified, and all input bytes remained unchanged. Dirty working files were excluded. The project's game/runtime tests were not run, so this is syntax/filesystem evidence only. The run began before the final class-helper naming repair.

The audit initially enabled an optional 120-second native traceback timer. macOS system Python 3.9.6 crashed while dumping a trace, and native sampling proved Python 3.13.7 was stuck in `dump_frame` while its main thread waited to cancel the diagnostic. Those runs are not counted as passing. The audit-added timer was removed; no test assertion or coverage threshold was relaxed. The final supported interpreters completed without that timer.

## Assessment by dimension

| Dimension | Status | Evidence and remaining recommendation |
|---|---|---|
| Correctness | Significant issues | Reproduced failures repaired; dynamic Python semantics still exceed the stated static/sampled guarantees. Keep experimental status and run consumer-project tests. |
| Architecture/design | Significant issues | Engine and unifier remain large; proposal materialization mutates caller-owned ASTs (`refactor_engine.py:2582`, `:2703`; `models.py:99`). Separate immutable planning from application. |
| Code quality | Significant issues | Repeated helper/progress/import logic and broad exception paths remain. Some internal exceptions become silent candidate rejection (`refactor_engine.py:1804`, `:1837`). Distinguish expected rejection from engine failure. |
| Type safety | Minor issues | Strict checks pass, but dynamic AST/tool boundaries retain `Any` and casts. Strict mode is not a claim that all values are statically precise. |
| Testing | Significant issues | Oracle holes repaired and executable regressions added. Sampling cannot establish arbitrary reflection, callbacks, external effects, or all closure/class behavior. Complex constructors/inheritance unsupported by the generic adapter are reported as unverified; dedicated tests cover selected inherited cases. Cross-file returned closures are unsupported. Single-file comparison samples one returned-callable layer; deeper returned callables are not validated, so a pass does not establish their behavior. |
| Robustness | Significant issues | Syntax/path/cache checks improved. Multi-file writes are not transactional, and ordinary write calls may leave partial updates on I/O failure (`refactor_engine.py:2975`, `:3123`; rename writes in `cli.py`). |
| Security | Minor issues | No detected medium/high static findings or selected credential patterns. Input source should be trusted when running behavioral tests, which execute it. Private vulnerability reporting needs maintainer verification. |
| Dependencies | Minor issues | Minimum raised to Python 3.10 with maintainer approval; vulnerable legacy tool versions removed from the lock. Supported environment advisory checks pass. |
| Performance | Significant issues | Removed duplicate progress-only pairing; two large tautological tests now use precise fixtures. Candidate generation still repeats block/signature work and compares Cartesian products (`refactor_engine.py:1128`, `:1448`, `:1458`), approximately O(F²B²) before filtering. No general scalability claim. |
| Documentation | Minor issues | Current CLI, limitations, setup, and experimental status replace unconditional guarantees and stale counts. Historical notes describe earlier behavior and are labeled accordingly. |
| Packaging | Minor issues | Explicit distribution contents, modern license metadata, typed marker, and installed CLI smoke checks. Archive contents and installed CLI behavior verified. |
| License/provenance | Minor issues | Existing declaration preserved; selected history screened. Maintainer owns publication scope and contribution provenance review. |
| Release readiness | Significant issues | Experimental candidate only; local gates pass. Choose a release version and verify reporting/support policy before publication. |

Additional design evidence: the process-global analysis cache remains unbounded and is accessed privately by the engine (`pipeline.py:236`, `refactor_engine.py:3286`). It is not designed for concurrent threads. Legacy `ast_normalizer.py:102` and `:192` still expose unsafe syntactic transformations for compatibility, although the production pipeline no longer imports them and README warns about them.

## Legacy development-toolchain finding

**Resolved by the approved Python 3.10 minimum.** The earlier Python 3.9 development environment selected Black 25.11.0 (PYSEC-2026-2120/2121), Click 8.1.8 (PYSEC-2026-2132), filelock 3.19.1 (PYSEC-2026-1374/1375), and pytest 8.4.2 (PYSEC-2026-1845). These are development dependencies, not Towel runtime dependencies. Findings concern formatter option/action handling, editor invocation, symlink races, and predictable temporary directories; no exploitation or reachability through Towel's runtime was established.

The patched releases require Python 3.10 or newer: [Black 26.3.1](https://pypi.org/project/black/26.3.1/), [Click 8.3.3](https://pypi.org/project/click/8.3.3/), [filelock 3.20.3](https://pypi.org/project/filelock/3.20.3/), and [pytest 9.0.3](https://pypi.org/project/pytest/9.0.3/). The maintainer approved raising the minimum to Python 3.10. The project metadata, CI matrix, Black targets, and regenerated lockfile implement that decision; direct Black/pytest minimums also exclude the affected versions. Quality tooling should run on the documented Python 3.13 environment. The final matrix uses freshly allocated private temporary directories for pytest; this reduces temporary-directory exposure but does not erase the advisory findings.

## Exposure screening and limits

Selected private-key, AWS, GitHub, OpenAI, PyPI, and Slack credential patterns produced no matches across **99 reachable commits and 911 historical blob versions**. No historical `.env`, `.netrc`, private-key, or credentials paths were found. Baseline committed text screening found no selected employer/internal-domain or local-user-path markers. The copied pending historical bug handoff does contain local paths; baseline screening must not be misrepresented as a claim about every new document.

The original untracked `.env` and ignored application fixture were not copied into the audit worktree. Historical local notes and `.todo` content remain in Git history; nothing was deleted or rewritten. Source distributions exclude local notes/state, caches, and environment files. Pattern scans are heuristic and do not prove that all confidential content is absent. Final staged-content screening found no selected credential patterns in the 97 changed files.

## Five highest-impact follow-ups

1. **Transactional application:** stage complete changes, replace individual files atomically, and provide explicit recovery for partial multi-file failures.
2. **Consumer-project semantic validation:** run representative projects' own tests on disposable refactorings; turn any counterexamples into engine regressions and precise unsupported-subset rules.
3. **Immutable planning and cache ownership:** copy proposal ASTs before materialization, return an explicit change plan, and scope/limit caches to an analysis session.
4. **Indexed candidate generation and visible failures:** compute blocks/signatures once per function, bucket compatible candidates, and surface unexpected internal exceptions separately from conservative rejection.
5. **Public surface and policy:** formally deprecate or relocate unsafe legacy normalizers after compatibility review; choose a version and verify the vulnerability-reporting channel/support policy. Review historical notes for the intended public scope.

Do not advertise production stability or unconditional behavioral preservation. Publication was not performed by this audit.
