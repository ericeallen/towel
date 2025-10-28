# Comprehensive Testing Audit

## 1. Builtin Identifiers Coverage

### Currently Covered (57 items)
Functions, constants, and common exceptions in `src/towel/unification/builtins.py`

### Missing Builtins (67 items)

#### Exception Types (52)
- ArithmeticError, BaseExceptionGroup, BlockingIOError, BrokenPipeError, BufferError
- BytesWarning, ChildProcessError, ConnectionAbortedError, ConnectionError
- ConnectionRefusedError, ConnectionResetError, DeprecationWarning, EOFError
- EncodingWarning, EnvironmentError, ExceptionGroup, FileExistsError
- FileNotFoundError, FloatingPointError, FutureWarning, GeneratorExit, IOError
- ImportWarning, IndentationError, InterruptedError, IsADirectoryError
- KeyboardInterrupt, LookupError, MemoryError, ModuleNotFoundError
- NotADirectoryError, OSError, OverflowError, PendingDeprecationWarning
- PermissionError, ProcessLookupError, PythonFinalizationError, RecursionError
- ReferenceError, ResourceWarning, RuntimeWarning, StopAsyncIteration
- SyntaxError, SyntaxWarning, SystemError, SystemExit, TabError, TimeoutError
- UnboundLocalError, UnicodeDecodeError, UnicodeEncodeError, UnicodeError
- UnicodeTranslateError, UnicodeWarning, UserWarning, Warning, ZeroDivisionError

#### Functions (8)
- `aiter`, `anext` (async iteration, Python 3.10+)
- `breakpoint` (debugging, Python 3.7+)
- `copyright`, `credits`, `exit`, `license`, `quit` (interactive helpers)

#### Constants (2)
- `Ellipsis` (...)
- `NotImplemented`

**Action**: Add all missing builtins to `builtins.py`

---

## 2. Binding Constructs Coverage

### Currently Handled ✅
1. **Assignment**: `Assign`, `AnnAssign`, `AugAssign`
2. **Functions**: `FunctionDef`, `AsyncFunctionDef`, `Lambda`
3. **Classes**: `ClassDef`
4. **Loops**: `For`
5. **Comprehensions**: `ListComp`, `DictComp`, `SetComp`, `GeneratorExp`, `comprehension`
6. **Exceptions**: `ExceptHandler`
7. **Imports**: `Import`, `ImportFrom`

### Missing ❌
1. **Walrus operator**: `NamedExpr` (`:=`)
2. **Async loops**: `AsyncFor`
3. **Context managers**: `With`, `AsyncWith`, `withitem`
4. **Match statements**: `Match` and pattern bindings (Python 3.10+)

**Impact**: Code using these constructs may have incorrect free variable analysis!

---

## 3. Edge Case Test Values

### Current Testing
Uses basic values from `automatic_equivalence_tester.py`:
- Integers: 0, 1, -1, 5, 10
- Strings: '', 'test', 'a'
- Lists: [], [1, 2, 3]
- Dicts: {}, {'key': 'value'}
- Booleans: True, False

### Missing Edge Cases

#### Integers
- ❌ Boundary: `sys.maxsize`, `-sys.maxsize - 1`
- ❌ Large numbers: `10**100`

#### Floats
- ❌ Special values: `float('nan')`, `float('inf')`, `float('-inf')`
- ❌ Signed zeros: `0.0`, `-0.0`
- ❌ Smallest: `sys.float_info.min`, `sys.float_info.epsilon`
- ❌ Largest: `sys.float_info.max`
- ❌ Denormals: Very small subnormal numbers

#### Strings
- ❌ Unicode edge cases: emojis, combining characters, RTL text
- ❌ Very long strings (1MB+)
- ❌ Strings with null bytes
- ❌ Escape sequences: `'\n'`, `'\t'`, `'\\'`

#### Bytes
- ❌ Empty: `b''`
- ❌ With null bytes: `b'\x00'`
- ❌ Binary data

#### Collections
- ❌ Nested structures: `[[[[]]]]`
- ❌ Circular references (if applicable)
- ❌ Very large collections (10000+ elements)
- ❌ Mixed types: `[1, 'a', None, True]`

#### None and Ellipsis
- ❌ `None` edge cases
- ❌ `...` (Ellipsis) usage

**Action**: Create comprehensive edge case test value generator

---

## 4. Syntactic Construct Coverage

### Expression Nodes
Need test cases ensuring we handle:
- ✅ BinOp, UnaryOp, BoolOp, Compare
- ✅ Call, Attribute, Subscript
- ✅ List, Tuple, Set, Dict
- ✅ ListComp, DictComp, SetComp, GeneratorExp
- ✅ Lambda
- ❌ **NamedExpr** (walrus)
- ✅ IfExp (ternary)
- ✅ Starred, Slice
- ❌ **Await** (async)
- ❌ **Yield**, **YieldFrom** (generators)
- ❌ **FormattedValue**, **JoinedStr** (f-strings - need more tests)

### Statement Nodes
- ✅ Assign, AnnAssign, AugAssign
- ✅ For, While
- ❌ **AsyncFor**, **AsyncWith**
- ✅ If, With
- ✅ Try, Raise, Assert
- ✅ Import, ImportFrom
- ✅ FunctionDef, ClassDef
- ❌ **AsyncFunctionDef** (needs dedicated tests)
- ❌ **Match** (Python 3.10+)
- ❌ **Global**, **Nonlocal** (scope modifiers)
- ❌ **Delete**
- ✅ Return, Pass, Break, Continue

**Action**: Create test cases for each missing construct

---

## 5. Binding Capture Issues

### Scenarios to Test
Each binding construct should have tests for:

1. **Shadowing**: Binding shadows outer variable
   ```python
   x = 1
   for x in [2, 3]:  # Shadows outer x
       pass
   ```

2. **Nested scopes**: Multiple levels of binding
   ```python
   for i in range(10):
       for i in range(5):  # Shadows loop variable
           pass
   ```

3. **Comprehension isolation**: Variables don't leak
   ```python
   [x for x in range(10)]
   # x should not exist here
   ```

4. **Exception variable scoping**: Limited scope in Python 3
   ```python
   try:
       pass
   except Exception as e:
       pass
   # e doesn't exist here in Python 3
   ```

5. **With statement**: Context manager bindings
   ```python
   with open('file') as f:
       pass
   # f should still be accessible here (unlike exceptions)
   ```

6. **Walrus operator**: Leaks into enclosing scope
   ```python
   if (x := 10) > 5:
       pass
   # x should exist here
   ```

7. **Global/Nonlocal**: Modifies outer scopes
   ```python
   def outer():
       x = 1
       def inner():
           nonlocal x
           x = 2
   ```

**Action**: Create comprehensive binding capture test suite

---

## 6. Observational Equivalence Completeness

### Current Coverage
- 175/175 proposals passing
- 18 example files

### Gaps

#### Missing construct testing:
- Walrus operator (`:=`)
- Async constructs (`async def`, `async for`, `async with`, `await`)
- Match statements (Python 3.10+)
- Global/nonlocal scope modifiers
- Generators with `yield`/`yield from`
- Decorators with complex arguments
- Context managers (`with ... as`)

#### Missing edge case values:
- All the edge cases listed in section 3
- Functions that modify global state
- Functions with side effects (print, file I/O)
- Functions that raise different exception types

#### Missing error conditions:
- Division by zero
- Index out of bounds
- Key errors in dicts
- Attribute errors
- Type errors from operations
- Recursion errors

**Action**: Create comprehensive stress test suite

---

## Priority Actions

### Critical (Security/Correctness) 🔴
1. **Fix missing binding constructs**: `NamedExpr`, `AsyncFor`, `With`, `AsyncWith`
2. **Add all exception types to builtins**: Prevent incorrect parameterization
3. **Test global/nonlocal**: These change semantics significantly

### High Priority (Completeness) 🟡
4. **Edge case test values**: Catch numeric/string edge cases
5. **Test all syntactic constructs**: Ensure we handle valid Python
6. **Comprehensive binding capture tests**: Prevent variable capture bugs

### Medium Priority (Robustness) 🟢
7. **Stress tests**: Large inputs, deeply nested structures
8. **Error condition tests**: Verify exceptions preserved correctly
9. **Side effect tests**: Verify I/O and state changes preserved

---

## Test Plan

### Phase 1: Fix Critical Gaps (Immediate)
1. Add missing builtins to `builtins.py`
2. Add `NamedExpr`, `AsyncFor`, `With`, `AsyncWith` to scope analyzer
3. Create test cases for these constructs
4. Verify observational equivalence still at 100%

### Phase 2: Comprehensive Edge Cases (Next)
1. Create `EdgeCaseValueGenerator` class
2. Integrate into `automatic_equivalence_tester.py`
3. Add edge case examples to `test_examples/`
4. Run full test suite

### Phase 3: Syntactic Coverage (Then)
1. Create test file for each missing construct
2. Add to observational equivalence testing
3. Document any constructs we intentionally don't support

### Phase 4: Stress Testing (Finally)
1. Create `test_stress.py` with pathological cases
2. Test with large inputs (1000+ lines)
3. Test with deeply nested structures
4. Performance benchmarking
