# AST Node Type Audit for Towel

## Overview

This document audits all Python AST node types to ensure Towel's `ParameterSubstituter` class (in `extractor.py`) correctly handles all binding and usage occurrences during code extraction.

## Audit Date

October 2025

## Python AST Node Types

### 1. BINDING CONSTRUCTS (Create new variable bindings)

These nodes introduce new variables into the scope. The `ParameterSubstituter` must NOT replace binding occurrences with parameters.

#### ✅ Assign
**Status**: Handled correctly
**Location**: `extractor.py:271-284` (`visit_Assign`)
**Behavior**: Visits RHS value, does NOT transform LHS targets (they're bindings)

#### ✅ AugAssign (+=, -=, etc.)
**Status**: Handled correctly (AFTER FIX)
**Location**: `extractor.py:286-312` (`visit_AugAssign`)
**Behavior**: Special case - target is BOTH read and write. Checks if target should be parameterized and renames it appropriately. The target has Store context but is actually being read.
**Note**: Also requires free variable protection in `refactor_engine.py:468-507`

#### ❓ AnnAssign (Annotated assignment: `x: int = 5`)
**Status**: NEEDS REVIEW
**Current**: No special visitor method - falls back to `generic_visit`
**Risk**: LOW - annotated assignments are similar to regular assignments
**Action**: Should add `visit_AnnAssign` similar to `visit_Assign`

#### ✅ For / AsyncFor
**Status**: Handled correctly
**Location**: `extractor.py:225-247` (`visit_For`)
**Behavior**: Iterator is transformed, target (loop variable) is NOT transformed (it's a binding), body is transformed

#### ✅ With / AsyncWith
**Status**: Not explicitly handled, but covered by `_add_assignment_bindings` in scope_analyzer
**Risk**: LOW - with statements create bindings via `optional_vars`

#### ✅ comprehension (for in list/dict/set comprehensions)
**Status**: Handled correctly
**Location**: `extractor.py:249-269` (`visit_comprehension`)
**Behavior**: Iterator is transformed, target is NOT transformed (binding within comprehension scope)

#### ❓ NamedExpr (Walrus operator: `:=`)
**Status**: NEEDS REVIEW
**Current**: No special visitor method in `ParameterSubstituter`
**Risk**: MEDIUM - walrus operator creates bindings that leak into enclosing scope
**Action**: Should add `visit_NamedExpr` to handle target (binding) vs value (expression)

#### ✅ ExceptHandler (`except ValueError as e:`)
**Status**: Covered by scope_analyzer, no special handling needed in extractor
**Risk**: LOW

#### ✅ FunctionDef / AsyncFunctionDef / Lambda / ClassDef
**Status**: Not descended into (correct behavior)
**Location**: `extractor.py:397-403` (`visit_FunctionDef`, `visit_AsyncFunctionDef`)
**Behavior**: Nested function/class definitions are not visited (they have separate scopes)

#### ✅ Import / ImportFrom
**Status**: Covered by scope_analyzer
**Risk**: LOW

### 2. USAGE CONSTRUCTS (Reference existing variables)

These nodes use variables without creating new bindings.

#### ✅ Name (with Load context)
**Status**: Handled correctly
**Location**: `extractor.py:314-356` (`visit` method checks for parameterization)
**Behavior**: Checks if expression should be replaced with parameter, but skips Store/Del contexts

#### ✅ Attribute access (`obj.attr`)
**Status**: Handled correctly via generic_visit
**Risk**: LOW - only the base object needs to be parameterized, not the attribute name

#### ✅ Subscript (`list[0]`, `dict[key]`)
**Status**: Handled correctly via generic_visit
**Risk**: LOW - value and slice are visited, but subscript itself isn't replaced

### 3. EXPRESSION CONSTRUCTS

#### ✅ BinOp / UnaryOp / Compare / BoolOp
**Status**: Handled correctly via generic_visit
**Risk**: LOW - operands are visited recursively

#### ✅ Call
**Status**: Handled correctly via generic_visit
**Risk**: LOW - func and args are visited

#### ❓ Lambda
**Status**: NEEDS REVIEW
**Current**: Has `visit_FunctionDef` and `visit_AsyncFunctionDef` but NO `visit_Lambda`
**Risk**: MEDIUM - Lambdas create nested scopes like functions
**Action**: Should add `visit_Lambda` to avoid descending into lambda bodies (separate scope)

#### ✅ IfExp (ternary: `a if condition else b`)
**Status**: Handled correctly via generic_visit
**Risk**: LOW

#### ✅ ListComp / SetComp / DictComp / GeneratorExp
**Status**: Not explicitly handled, but should be
**Risk**: HIGH - Comprehensions have their own scope in Python 3+
**Action**: Should NOT descend into comprehension bodies or should handle scope correctly

### 4. STATEMENT CONSTRUCTS

#### ✅ Return / Yield / YieldFrom
**Status**: Handled correctly via generic_visit
**Risk**: LOW - value is visited

#### ✅ If / While / Break / Continue / Pass
**Status**: Handled correctly
**Risk**: LOW

#### ✅ Try / Except / Finally / Raise
**Status**: Handled correctly via generic_visit
**Risk**: LOW

### 5. F-STRING CONSTRUCTS

#### ✅ JoinedStr (f-string: `f"Hello {name}"`)
**Status**: Handled correctly
**Location**: `extractor.py:203-223` (`visit_JoinedStr`)
**Behavior**: Special handling to avoid breaking f-string structure. Constant string parts stay as constants, FormattedValue expressions are parameterized.

#### ✅ FormattedValue
**Status**: Handled correctly within `visit_JoinedStr`
**Risk**: LOW

### 6. PATTERN MATCHING (Python 3.10+)

#### ❓ Match / case
**Status**: NEEDS REVIEW
**Current**: No special handling
**Risk**: MEDIUM - Pattern matching introduces bindings
**Action**: Should add visitors for match statements if targeting Python 3.10+

## Summary of Actions Needed

### HIGH PRIORITY

1. ❌ **Add `visit_Lambda` method** - Lambdas create nested scopes and should not be descended into, just like FunctionDef

2. ❌ **Add comprehension scope handling** - ListComp, SetComp, DictComp, GeneratorExp all create nested scopes in Python 3+. Need to either not descend or handle scoping correctly.

### MEDIUM PRIORITY

3. ⚠️ **Add `visit_NamedExpr` method** - Walrus operator creates bindings that need special handling

4. ⚠️ **Add `visit_AnnAssign` method** - Annotated assignments should be handled like regular assignments

5. ⚠️ **Match statements** - If supporting Python 3.10+, need to handle pattern matching

### ALREADY FIXED

✅ **AugAssign handling** - Fixed in extractor.py and refactor_engine.py (lines 468-507)

## Test Coverage

### Well-Tested Cases
- Basic assignments and augmented assignments
- For loops and comprehensions (loop variables)
- Function definitions (not descended)
- F-strings

### Needs More Testing
- Walrus operator (`:=`)
- Annotated assignments (`x: int = 5`)
- Lambda functions in extracted code
- Nested comprehensions
- Pattern matching (Python 3.10+)

## Recommendations

1. **Immediate**: Add `visit_Lambda` to prevent descending into lambda bodies
2. **Immediate**: Fix comprehension handling to respect their scope
3. **Short-term**: Add `visit_NamedExpr` and `visit_AnnAssign`
4. **Long-term**: Add comprehensive tests for all node types
5. **Long-term**: Consider pattern matching support for Python 3.10+

## Related Issues

- **Augmented Assignment Bug**: Fixed in this session (refactor_engine.py lines 468-507)
- **Multiple Returns**: Still needs investigation (tricky_edge_cases_adversarial.py)
- **Try/Finally Variables**: Related to free variable detection

## References

- Python AST documentation: https://docs.python.org/3/library/ast.html
- `extractor.py`: ParameterSubstituter class (lines 194-359)
- `scope_analyzer.py`: ScopeRespectingWalker class (lines 272-525)
