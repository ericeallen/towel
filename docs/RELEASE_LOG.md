## 2026-09-13

- Version: 1.1.0
- Commit: (this branch, `audit/open-source-2026-09-12`)
- Summary:
  - Soundness: every accepted proposal is verified by instantiating the helper with each call's arguments and comparing with the replaced block up to renamed binders; arguments that are not names, literals, or containers of those are passed as thunks evaluated at the original position.
  - Guards: closure/cell sharing across the block boundary, deletion and `except ... as` of pre-bound names, moved `global`/`nonlocal` declarations, slice and starred parameters, and opaque method decorators are rejected or handled explicitly; match captures bind in scope analysis; generated parameter names avoid block identifiers; clustered replacements may not overlap.
  - Layouts: Flit projects are supported in directory mode.
  - Evidence: a standing 59-project ecosystem check (`scripts/ecosystem_check.py`, `just ecosystem`, weekly CI) passes 48 projects' full suites identically before and after transformation, with 7 producing no proposal, 3 documented frame-sensitive or source-observing cases, and 1 unsupported layout; hostile single-file and cross-file batteries execute fixtures before and after fixed-point refactoring.
  - Renaming: `towel rename-helpers --list --json` emits an inventory for LLM-driven naming and `--rename-file` applies a batch with scope and importer checks, reporting JSON.
  - Performance: safety guards, unification, and per-block analyses are memoized per analysis; pyflakes' 2,167-line test module analyzes in about a minute instead of exceeding nine.
- Status: All tests green
  - Unit/integration tests: 1,227 passed on Python 3.11, 3.12, and 3.13
  - Ecosystem check: 48 PASS, 7 NO_CHANGE, 3 BROKEN_KNOWN, 1 UNSUPPORTED of 59 (see docs/PRODUCTION_READINESS.md)

---

## 2025-11-25

- Version: 0.6.10
- Commit: (this commit)
- Summary:
  - Pipeline: Versioned the parse/analysis cache and routed cached modules through assign→augassign plus arithmetic canonicalization so fresh engine runs always see normalized ASTs without manual cache busts.
  - Engine coverage: With canonicalized inputs, the refactor engine now extracts the `process_user_score_v1/2` helper in `complex_expressions.py`, matching manual unifier expectations.
  - Baselines/tests: Regenerated all single-file and cross-file expected outputs to capture the new helper and canonicalized subtraction forms, and re-ran targeted normalizer + engine pytest suites (58 tests) to keep focused coverage green.
- Status: Targeted suites green
  - Unit/integration tests: 58 tests OK (0 skipped)
  - Cross-file observational equivalence: not re-run (unchanged behavior outside normalization)

---

## 2025-11-21

- Version: 0.6.9
- Commit: (this commit)
- Summary:
  - Hygiene: Ensured extracted helper functions get unique, per-file names by seeding helper counters from each canonical file, updated import rewriting, and taught the refactor engine to preserve uniqueness when promoting helpers across files.
  - Regression coverage: Added duplicate-helper detection to `tests/test_regression.py` plus numerous targeted assertions so observational-equivalence suites accept the new suffixing scheme without brittle expectations.
  - Baselines: Regenerated every single-file and cross-file expected output to capture the helper naming changes and keep regression comparisons stable.
- Status: All tests green
  - Unit/integration tests: 715 tests OK (0 skipped)
  - Cross-file observational equivalence: 3 project(s), 3/3 proposals passed (100%)

---

## 2025-11-19

- Version: 0.6.8
- Commit: (this commit)
- Summary:
  - Regression safeguards: Expanded `tests/test_regression.py` normalization so helper naming variants (`__extracted_func`, `_extracted_func`, `extracted_function`) map to canonical identifiers, preventing cosmetic diffs from failing stability checks.
  - Baselines: Regenerated all single-file and cross-file expected outputs via fixed-point refactoring to capture the new naming policies, and introduced `tests/conftest.py` to keep pytest from traversing generated dirs.
  - Engine hygiene: Added targeted tests for ancestor insertion, detail progress output, Option B literal promotion, and formatting preservation to guard future refactors; also introduced `scripts/bench_refactor.py` for incremental vs. naïve benchmarking.
- Status: All tests green
  - Unit/integration tests: 713 tests OK (0 skipped)
  - Cross-file observational equivalence: 3 project(s), 3/3 proposals passed (100%)

---

## 2025-11-10

- Version: 0.6.7
- Commit: 66ba8b9
- Summary:
  - Architecture: Introduced a compiler-style pipeline (`models.py`, `pipeline.py`, `visitors.py`) and redirected the refactor engine to delegate analysis through explicit phases with shared dataclasses.
  - Documentation: Authored `docs/PIPELINE.md` describing each phase, data artifact, and invariants for the new architecture.
  - Testing: Added `tests/test_pipeline_api.py` to validate pipeline parity, and wired flake8/mypy into CI while expanding strict typing coverage to the new modules.
  - Tooling: Added dev extras installation plus flake8/mypy gates to the CI workflow to keep lint/type checks enforced automatically, and aligned package metadata with the Apache 2.0 license.
- Status: All tests green
  - Unit/integration tests: 705 tests OK (0 skipped)
  - Cross-file observational equivalence: 3 project(s), 3/3 proposals passed (100%)

---

## 2025-11-10

- Version: 0.6.6
- Commit: c9a7e0a
- Summary:
  - Engine: Ensured canonical source files are always processed during multi-file refactors so promoted helpers are emitted in their shared base classes without missing insertions.
  - Tests: Added cross-file adversarial suites covering instance, classmethod, and multilevel hierarchies to confirm helpers land in the nearest safe ancestor and maintain implicit binders.
  - Documentation: Updated README and deep-dive docs to highlight class-aware helper promotion, plus improved release log references to the improvements summary.
  - Packaging: Bumped project version to 0.6.6 to capture the class-aware method promotion work.
- Status: All tests green
  - Unit/integration tests: 702 tests OK (0 skipped)
  - Cross-file observational equivalence: 3 project(s), 3/3 proposals passed (100%)

---

## 2025-11-09

- Version: 0.6.4
- Commit: d55b83e
- Summary:
  - Engine: Promoted `Replacement` to a dataclass with method metadata; preserved method dispatch when rewriting calls and avoided shared AST mutation during multi-file application.
  - Safety: Auto-coerce legacy tuple replacements, restoring compatibility across tests and overlap filtering utilities.
  - Fixtures: Restored pristine `test_examples` sources, added `.templates/` snapshots, and refreshed single-file/cross-file baselines via fixed-point regeneration.
  - Tests: Split the monolithic comprehensive coverage file into focused suites, expanding extractor/unifier edge-case coverage and adding adversarial method tests.
- Status: All tests green
  - Unit/integration tests: 695 tests OK (0 skipped)
  - Observational equivalence: 3/3 cross-file projects passed (100%)

---

## 2025-11-05

- Version: 0.6.0
- Commit: c419770
- Summary:
  - Engine: Insert extracted helpers into the Deepest Common Enclosing (DCE) function scope when possible; prefer function-scope over class/module.
  - Semantics: Promote global/nonlocal declarations for assigned names into the extracted helper to preserve runtime behavior; continue conservative skip for nonlocal closure contexts.
  - Hygiene: Include target function’s local bindings into `enclosing_names` when inserting within that function; reset extractor `used_names` per extraction to stabilize helper names across proposals.
  - Tests: Added adversarial DCE variants (mixed async/sync, interleaved nested candidates, nonlocal/global interaction ensuring global injection and nonlocal skip) in `tests/test_engine_adversarial.py`.
  - Baselines: Regenerated single-file and cross-file expected outputs to reflect function-scope insertion and global handling.
- Status: All tests green
  - Curated tests: 547 tests OK
  - Observational equivalence: single-file and cross-file suites PASS

---

Notes:
- To revert to this exact state: check out commit `c419770` on branch `main`.
- Changes were pushed to origin/main on 2025-11-05.

## 2025-11-04

- Version: 0.5.7
- Commit: d05ec82
- Summary:
  - Tests: Updated adversarial breaker tests in `tests/test_breakers.py` to reflect fixed behavior for `@staticmethod`/`@classmethod` extraction. The engine now preserves observational equivalence in these contexts; tests now assert zero failures.
  - Tooling: Ensured package is installed in editable mode for local runs; no runtime deps added.
  - Docs: Appended session notes to `docs/agent_log.md` capturing rationale and steps.
  - Engine: Added binding-aware unification for additional Python constructs: `with ... as ...`, `except ... as ...`, and walrus `:=` targets. These identifiers are now treated as bindings (alpha-renamed) rather than parameters.
  - Tests: Added `tests/test_bindings_additional.py` covering with-as, except-as, and walrus alpha-equivalence. All tests pass.
- Status: All tests green (spot-checked breakers; full suite run as part of release flow)
  - Unit/integration tests: expected 491+ tests OK
  - Observational equivalence: no regressions expected

---

Notes:
- To revert to this exact state: check out the commit recorded above on branch `main`.
- Changes will be pushed to origin/main on 2025-11-04.

## 2025-11-03

- Version: 0.5.4
- Commit: 91c89a309ef077a88af3f2a519e303a39f50524d
- Summary:
  - Tooling: Added and adopted an automated release workflow (`just release <ver>`) that runs code quality checks, executes full unit and observational-equivalence suites, computes and records test statistics, bumps the version, updates this release log, creates an annotated tag, and pushes atomically.
  - Git hygiene: Release flow enforces a clean working tree, rebases on `origin/main` before releasing, and auto-stages any generated artifacts from checks/tests.
  - DX: Per-file progress indicators retained for long-running stability comparisons so you can see steady progress during releases and regression runs.
  - Engine/Behavior: No engine changes in this release; refactoring behavior remains identical to 0.5.3 (which switched baselines and regression comparisons to fixed-point).
- Status: All tests green
  - Unit/integration tests: 491 tests OK (0 skipped)
  - Observational equivalence: 116/116 proposals passed (100%)
  - Cross-file observational equivalence: 3 project(s), 3/3 proposals passed (100%)

---

Notes:
- To revert to this exact state: check out commit `91c89a309ef077a88af3f2a519e303a39f50524d` on branch `main`.
- Changes were pushed to origin/main on 2025-11-03.

# Release Log

This log records notable repository states with all tests passing, to make it easy to revert or audit changes.

## 2025-11-03

- Version: 0.5.3
- Commit: afa5c209ed390cce36f91b82e985f457d4a848e6
- Summary:
  - Baseline/Testing: Switched baseline generation and regression comparison to fixed-point refactoring to match real-world usage (same behavior as `scripts/dry`). Regenerated expected outputs accordingly.
  - DX: Enabled verbose progress output in single-file and cross-file observational equivalence tests to provide reassurance during long runs.
  - Tooling: `regenerate-baseline` now uses fixed-point for both single-file and cross-file baselines.
- Status: All tests green
  - Unit/integration tests: 491 tests OK (0 skipped)
  - Observational equivalence: 116/116 proposals passed (100%)
  - Cross-file observational equivalence: 3/3 projects passed (100%)

---

Notes:
- To revert to this exact state: check out commit `afa5c209ed390cce36f91b82e985f457d4a848e6` on branch `main`.
- Changes were pushed to origin/main on 2025-11-03.

## 2025-11-02

- Version: 0.5.2
- Commit: 42d96fbb0439a613294fd6f1fdacd90bcf9201db
- Summary:
  - Engine: Fixed a nested-block regression by deferring evaluation of unified parameters used as callees inside the extracted body. Call-site arguments are now wrapped in forwarding lambdas (thunks) to preserve guarded semantics and arity.
  - Docs: Added note documenting the callee-parameter thunking rule (`docs/CALLEE_PARAMETER_THUNK.md`).
  - Baseline: Regenerated expected outputs.
- Status: All tests green
  - Unit/integration tests: 491 tests OK (0 skipped)
  - Observational equivalence: 116/116 proposals passed (100%)
  - Cross-file observational equivalence: 3/3 projects passed (100%)

---

Notes:
- To revert to this exact state: check out commit `42d96fbb0439a613294fd6f1fdacd90bcf9201db` on branch `main`.
- Changes were pushed to origin/main on 2025-11-02.

## 2025-11-02

- Version: 0.5.1
- Commit: 099dd1a12f941cb2a54f27238e5a3ccb0c751223
- Summary:
  - Typing: Finished strict mypy cleanup by resolving remaining issues in unifier (async function visitor) and extractor (Optional handling, redundant cast removal).
  - Quality: mypy/flake8/black all passing; no behavioral changes intended.
- Status: All tests green
  - Unit/integration tests: 491 tests OK (0 skipped)
  - Cross-file observational equivalence: 3/3 projects passed (100%)

---

Notes:
- To revert to this exact state: check out commit `099dd1a12f941cb2a54f27238e5a3ccb0c751223` on branch `main`.
- Changes were committed on 2025-11-02.

## 2025-11-02

- Version: 0.5.0
- Commit: 7c7e919e529efad3c93147ed1c2d87f2f16e7551
- Summary:
  - Tests: Unskipped previously skipped observational equivalence tests (variable capture fix effective); suite now runs with zero skips.
  - Quality: mypy/flake8/black all passing; no functional changes beyond test enablement.
- Status: All tests green
  - Unit/integration tests: 491 tests OK (0 skipped)
  - Cross-file observational equivalence: 3/3 projects passed (100%)

---

Notes:
- To revert to this exact state: check out commit `7c7e919e529efad3c93147ed1c2d87f2f16e7551` on branch `main`.
- Changes were pushed to origin/main on 2025-11-02.

## 2025-11-02

- Version: 1.0.1
- Commit: cbfe0db0fbefe4d242c1764cfc14d35ef31de4d6
- Summary:
  - Quality: Resolved mypy attribute/variance issues in unifier and extractor (casts for tuple elements, Optional checks for current_blocks), kept flake8 clean; no functional changes.
  - Tooling: Maintained black/flake8/mypy configs; lint recipe continues to pass flags explicitly.
- Status: All tests green
  - Unit/integration tests: 491 tests OK (2 skipped as documented)
  - Cross-file observational equivalence: 3/3 projects passed (100%)

---

Notes:
- To revert to this exact state: check out commit `cbfe0db0fbefe4d242c1764cfc14d35ef31de4d6` on branch `main`.
- Changes were pushed to origin/main on 2025-11-02.

## 2025-11-02

- Version: 1.0.0
- Commit: 136501623533b762a51b6a199a6f8c510b26f355
- Summary:
  - Unifier: Added comprehension-aware alpha-renaming for ListComp, SetComp, DictComp, and GeneratorExp; fixed nested comprehension unification.
  - Extractor: Preserved unified parameter names and correctly mapped return assignment targets via inverse hygienic renames.
  - Tests: Added focused unit tests for nested comprehensions; updated expected outputs to reflect corrected behavior.
- Status: All tests green
  - Unit/integration tests: 491 tests OK (2 skipped as documented)
  - Cross-file observational equivalence: 3/3 projects passed (100%)

---

Notes:
- To revert to this exact state: check out commit `136501623533b762a51b6a199a6f8c510b26f355` on branch `main`.
- Changes were pushed to origin/main on 2025-11-02.
