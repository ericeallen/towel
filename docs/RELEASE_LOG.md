# Release Log

This log records notable repository states with all tests passing, to make it easy to revert or audit changes.

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
