# Comprehension Binding and Nested Comprehensions

Date: 2025-11-02

We improved the unifier to handle comprehension-bound variables (e.g., `for x in ...`) as alpha-equivalent across blocks, including nested comprehensions.

## What changed

- Added comprehension-aware alpha-renaming for:
  - List comprehensions (`ListComp`)
  - Set comprehensions (`SetComp`)
  - Dict comprehensions (`DictComp`)
  - Generator expressions (`GeneratorExp`)
- Targets in each generator (e.g., `x` in `for x in xs`) are treated like loop bindings, not parameters.
- Alpha-renaming remains active across the entire comprehension (generators and element/key/value), enabling nested cases like:

```python
# A
result = [[cell * 2 for cell in row] for row in matrix]

# B
result = [[item * 2 for item in line] for line in matrix]
```

## Why it matters

- Prevents invalid parameterization of comprehension variables (not available at call sites)
- Enables unification of structurally equivalent comprehensions with different bound variable names
- Avoids false negatives for nested comprehensions

## Tests

- New focused unit tests: `tests/test_unifier_comprehensions.py`
  - Ensures nested comprehension unification succeeds without parameters
- Existing bindings tests now include proposals for nested comprehension pairs

## Related extractor adjustment

- Preserved unified parameter names to align with `Substitution` keys
- Correctly map return assignment targets to per-block original names using inverse hygienic renames

## Status

- All tests pass; cross-file observational equivalence remains 100% on provided fixtures.
