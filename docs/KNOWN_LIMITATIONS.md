# Known Limitations

## Overview

This document tracks known limitations and edge cases in Towel's code extraction functionality. These are cases where the tool either produces incorrect refactorings or conservatively rejects valid extractions.

## Current Limitations

### 1. Complex Lambda Parameters

**Status**: Known Limitation (conservatively rejected)

**Description**:

Towel fully supports standard lambda expressions with simple parameters:
```python
# ✅ Fully supported - these WILL be unified:
map_a = lambda x: x * 2
map_b = lambda y: y * 2

filter_a = lambda x, threshold: x > threshold
filter_b = lambda y, limit: y > limit
```

However, Towel currently does not support unifying lambda expressions that use Python's advanced parameter features:
- Positional-only parameters (`/` syntax, Python 3.8+)
- Keyword-only parameters (`*` syntax)
- Variable positional arguments (`*args`)
- Variable keyword arguments (`**kwargs`)

**Examples of Unsupported Cases**:

```python
# ❌ Not currently unified:
process_a = lambda x, *, key=None: x if key else -x
process_b = lambda y, *, key=None: y if key else -y

# ❌ Not currently unified:
compute_a = lambda *args: sum(args) + 10
compute_b = lambda *args: sum(args) + 20

# ❌ Not currently unified:
func_a = lambda x, /, **kwargs: x + sum(kwargs.values())
func_b = lambda y, /, **kwargs: y + sum(kwargs.values())
```

**Rationale**:

These advanced parameter types require more sophisticated alpha-renaming and parameter mapping logic. Since they are relatively uncommon in typical duplicate code patterns (most lambda expressions use simple parameters), Towel conservatively rejects these cases rather than risk incorrect unification.

**Workaround**:

Convert complex lambdas to named functions before running Towel:

```python
# Before:
process_a = lambda *args, **kwargs: helper(*args, **kwargs) + 10
process_b = lambda *args, **kwargs: helper(*args, **kwargs) + 20

# After conversion (can be unified):
def process_a(*args, **kwargs):
    return helper(*args, **kwargs) + 10

def process_b(*args, **kwargs):
    return helper(*args, **kwargs) + 20
```

Regular functions with these parameter types ARE fully supported by Towel - only lambda expressions with these features are affected.

**Implementation Reference**: `src/towel/unification/unifier.py:1454`

---

## Resolved Issues (Historical Reference)

### Variable Rebinding in Extracted Blocks (Fixed in December 2025)

- **Summary**: Earlier versions substituted parameter names even after a local rebinding (e.g., returning `__param_0` instead of the updated `result`).
- **Resolution**: `ParameterSubstituter` now tracks statement order and stops substituting any variable once it is rebound inside the extracted block, including across branches and async constructs.
- **Regression Coverage**: `tests/test_extractor_comprehensive.py::test_rebinding_stops_parameter_substitution` and the adversarial scenarios in `test_examples/tricky_edge_cases_adversarial.py`.
- **Status**: Fully passing—no remaining observational-equivalence failures tied to this behavior.

---

## Statistics

**Overall Success Rate**: 100% (209/209 proposals pass observational equivalence tests)

**Adversarial Test Results**:
- control_flow_adversarial.py: 6/6 (100%)
- exception_adversarial.py: 2/2 (100%)
- side_effects_adversarial.py: 4/4 (100%)
- tricky_edge_cases_adversarial.py: 3/3 (100%)

**Date**: December 2025
