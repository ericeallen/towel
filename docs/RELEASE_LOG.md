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
