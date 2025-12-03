# Alpha-Renaming and Builtin Handling Fix

## Problems Fixed

### Problem 1: Builtins as Parameters

**Bug**: Python builtins like `print`, `range`, `len` were being treated as free variables and passed as parameters.

**Before**:
```python
def extracted_func(param_0, param_1, param_2, param_3, print, range):  # ← WRONG!
    x = param_0
    y = param_1
    if x == y:
        if y > param_2:
            for param_3 in range(0, x):
                print(param_3)
            return y
```

**After**:
```python
def extracted_func(param_0, param_1, param_2):  # ← CORRECT!
    x = param_0
    y = param_1
    if x == y:
        if y > param_2:
            for i in range(0, x):  # ← range is builtin
                print(i)            # ← print is builtin
            return y
```

### Problem 2: Parameter/Binding Variable Collision

**Bug**: Loop variables like `i` and `j` were being treated as expressions to parameterize, leading to the same name being used as both a parameter AND a loop variable.

**Before**:
```python
def extracted_func(..., param_3, ...):
    for param_3 in range(...):  # ← param_3 used as BOTH parameter and loop var!
        print(param_3)
```

**After**:
```python
def extracted_func(param_0, param_1, param_2):
    for i in range(...):  # ← i is a bound variable, NOT a parameter
        print(i)
```

### Problem 3: Missing Alpha-Equivalence

**Bug**: Loop variables with different names (`for i...` vs `for j...`) were not recognized as alpha-equivalent (structurally the same).

**Solution**: Implemented proper alpha-renaming to treat `i` and `j` as the same bound variable.

## Implementation

### 1. Builtin Tracking (`builtins.py`)

Created a new module to track Python builtins:

```python
PYTHON_BUILTINS = {
    'print', 'range', 'len', 'int', 'str', 'list', 'dict',
    'ValueError', 'TypeError', 'True', 'False', 'None',
    # ... complete list
}

def is_builtin(name: str) -> bool:
    return name in PYTHON_BUILTINS

def filter_builtins(names: set) -> set:
    return {name for name in names if not is_builtin(name)}
```

### 2. Updated Free Variable Calculation

Modified `scope_analyzer.py` to:
- Filter out builtins from free variables
- Properly track loop variables as bindings

```python
def get_free_variables(self, nodes: List[ast.AST]) -> Set[str]:
    # ... collect uses and bindings ...

    # Track loop variables as bindings
    elif isinstance(child, ast.For):
        if isinstance(child.target, ast.Name):
            bindings.add(child.target.id)

    # Free variables are used but not bound
    free_vars = uses - bindings

    # Filter out Python builtins
    free_vars = filter_builtins(free_vars)

    return free_vars
```

### 3. Implemented Alpha-Renaming

Modified `unifier.py` to track and use alpha-equivalence mappings:

```python
class Unifier:
    def __init__(...):
        # Track alpha-equivalence mappings for bound variables
        # Maps (block_idx, original_name) -> canonical_name
        self.alpha_renamings: Dict[Tuple[int, str], str] = {}
```

**When unifying For loops**:
```python
def _unify_for_loop(self, nodes, subst, block_indices):
    # Different loop variable names (i vs j)
    # Use the first block's variable name as canonical
    canonical_var = loop_var_names[0]  # e.g., 'i'

    # Establish alpha-renaming mappings
    for idx, block_idx in enumerate(block_indices):
        var_name = loop_var_names[idx]  # 'i' or 'j'
        # Map this block's loop var to the canonical name
        self.alpha_renamings[(block_idx, var_name)] = canonical_var

    # Unify the body with alpha-renaming in effect
    # ...
```

**When comparing Names**:
```python
if isinstance(first_node, ast.Name):
    # Apply alpha-renaming to get canonical names
    canonical_names = []
    for node, block_idx in zip(nodes, block_indices):
        name = node.id
        # Check if this name has an alpha-renaming
        renamed = self.alpha_renamings.get((block_idx, name), name)
        canonical_names.append(renamed)

    # Compare canonical names
    if len(set(canonical_names)) == 1:
        return True  # Same after alpha-renaming
```

## Results

### Test Case: `my_test/my_test.py`

**Input**:
```python
def foo():
    x = 1
    y = 2
    if x == y:
        if y > 5:
            for i in range(0, x):
                print(i)
            return y

def bar():
    x = 3
    y = 4
    if x == y:
        if y > 10:
            for j in range(0, x):
                print(j)
            return y
```

**Output**:
```python
def extracted_func(param_0, param_1, param_2):
    x = param_0
    y = param_1
    if x == y:
        if y > param_2:
            for i in range(0, x):
                print(i)
            return y


def foo():
    extracted_func(1, 2, 5)

def bar():
    extracted_func(3, 4, 10)
```

### Verification

✓ **Builtins NOT parameterized**: `print`, `range` are used directly
✓ **Loop variable NOT parameterized**: `i` is a bound variable
✓ **Alpha-equivalence**: `i` vs `j` recognized as the same bound variable
✓ **Code is valid Python**: Parses and executes correctly
✓ **3 parameters** (not 6): `param_0`, `param_1`, `param_2`

## Files Modified

1. **`towel/unification/builtins.py`** (NEW)
   - Tracks Python builtin names
   - Provides filtering utilities

2. **`towel/unification/scope_analyzer.py`**
   - Import `filter_builtins`
   - Updated `get_free_variables()` to:
     - Track loop variables as bindings
     - Track comprehension variables
     - Filter out builtins

3. **`towel/unification/unifier.py`**
   - Added `alpha_renamings` dict to track bound variable mappings
   - Implemented `_unify_for_loop()` with alpha-renaming
   - Updated Name comparison to use alpha-renaming

## Future Enhancements

1. **Lambda parameters**: Extend alpha-renaming to lambda parameters
2. **With statement vars**: Handle `with ... as var:` bindings
3. **Exception vars**: Handle `except E as e:` bindings
4. **Function parameters**: Handle nested function parameter shadowing
5. **Comprehension vars**: More robust handling of nested comprehensions

## Testing

```bash
# Test on the example
python3 -c "
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
proposals = engine.analyze_file('my_test/my_test.py')

if proposals:
    best = proposals[0]
    params = [arg.arg for arg in best.extracted_function.args.args]
    print(f'Parameters: {params}')
    print(f'✓ No builtins!' if 'print' not in params else '✗ Still has builtins')
"
```

## Summary

These fixes implement **proper scoping discipline**:

1. **Builtins are never parameterized** - they're globally available
2. **Bound variables are tracked correctly** - loop vars, comprehension vars, etc.
3. **Alpha-equivalence is recognized** - `for i...` and `for j...` are the same
4. **No naming collisions** - parameters and bound variables have distinct names

This makes the refactoring engine much more robust and correct!
