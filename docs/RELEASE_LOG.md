## 2025-11-03

- Version: 0.5.4
- Commit: aafd6f4db05716efea12b9c5a7f5cce9c687fe07
- Summary:
  - Summary of changes here.
- Status: All tests green
  - Unit/integration tests: 0 tests OK (0 skipped)
  - Observational equivalence: 116/116 proposals passed (100%)
  - Cross-file observational equivalence: Found
Found
Found
3 project(s), Python
Python
Python
3/3
2
2
3 proposals passed (0%)

---

Notes:
- To revert to this exact state: check out commit `aafd6f4db05716efea12b9c5a7f5cce9c687fe07` on branch `main`.
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
