# Known Limitations

## Overview

This document tracks known limitations and edge cases in Towel's code extraction functionality. These are cases where the tool either produces incorrect refactorings or conservatively rejects valid extractions.

## Current Limitations

### 1. Variable Rebinding in Extracted Blocks

**Status**: Known Bug (1 test failing out of 209 total)

**Affected Test**: `tricky_edge_cases_adversarial.py::conditional_return_a/b`

**Description**:

When extracting code that contains a variable that is:
1. Initialized BEFORE the extracted block
2. Used in the extracted block
3. RE-BOUND (reassigned) within the extracted block
4. Used again AFTER the rebinding

Towel may incorrectly substitute the variable with a parameter name even after the rebinding, leading to incorrect behavior.

**Example**:

```python
# Original code:
def conditional_return_a(x, threshold):
    result = x * 2        # Initialized before block
    if result > threshold: # Start of extracted block
        return result
    result = result + 10   # RE-BOUND within block
    return result          # Uses the re-bound value

# Buggy extracted function (what Towel currently generates):
def extracted_func(__param_0, __param_1):
    if __param_0 > __param_1:
        return __param_0
    result = __param_0 + 10
    return __param_0  # ❌ BUG! Should return 'result', not '__param_0'

# With args (0, 0):
# Expected: 10 (result = 0 + 10)
# Actual: 0 (__param_0 = 0)
```

**Root Cause**:

The parameter substitution logic in `extractor.py` (`ParameterSubstituter` class) doesn't track WHEN variables are bound during traversal. It makes a single pass over the AST and replaces all occurrences of a variable with its parameter name, without considering that:

1. Uses BEFORE a rebinding should be replaced with the parameter
2. Uses AFTER a rebinding should refer to the new local variable

This requires control-flow aware analysis to track which variables have been rebound at each point in the code.

**Why The Simple Fix Doesn't Work**:

An attempted fix that tracked ALL bound variables and prevented their parameterization was too broad:
- It prevented `result` from being used in the INITIAL uses (before rebinding)
- This caused `UnboundLocalError` because `result` was never passed as a parameter
- The fix was reverted

**What Would Be Needed to Fix It Properly**:

A proper fix would require:

1. **Statement-order tracking**: The `ParameterSubstituter` would need to track which statements have been visited so far during traversal
2. **Rebinding detection**: Identify assignment statements that rebind variables that were previously parameters
3. **Context-sensitive substitution**: After a rebinding statement, stop substituting that variable with the parameter name
4. **Control-flow awareness**: Handle cases where rebinding happens in one branch but not another (if/else, try/except, etc.)

This is complex because:
- AST transformation in Python doesn't naturally preserve statement ordering context
- Control flow makes it hard to know which rebindings affect which uses
- The same variable name might refer to different values at different points

**Workaround**:

For now, Towel will occasionally propose extractions for code with variable rebinding that produce incorrect results. Users should:
1. Review the extracted code carefully
2. Run tests to verify observational equivalence
3. Reject proposals that fail tests

The good news: This affects less than 0.5% of proposals (1 out of 209 test cases).

**Related Issues**:

- This is different from the augmented assignment bug (which was fixed)
- Augmented assignments (`total += 1`) were handled by ensuring the variable is passed as a parameter
- This bug is about what happens AFTER a variable is re-bound

---

## Statistics

**Overall Success Rate**: 99.5% (208/209 proposals pass observational equivalence tests)

**Adversarial Test Results**:
- control_flow_adversarial.py: 6/6 (100%)
- exception_adversarial.py: 2/2 (100%)
- side_effects_adversarial.py: 4/4 (100%)
- tricky_edge_cases_adversarial.py: 2/3 (66.7%) ← Contains the rebinding bug

**Date**: October 2025
