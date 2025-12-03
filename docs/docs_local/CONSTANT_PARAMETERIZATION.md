# Constant Parameterization

## Overview

The DRY detector can now parameterize differing **constants** in addition to variable names. This allows detection of "templated duplicates" - code that differs only in magic numbers, configuration values, or thresholds.

## Feature

Previously, the unifier would ONLY parameterize identifiers (variable names). Now it can also parameterize constants like numbers and strings.

### Before

```python
def foo():
    x = 1      # ← Constant
    y = 2      # ← Constant
    return x + y

def bar():
    x = 3      # ← Different constant
    y = 4      # ← Different constant
    return x + y
```

**Old behavior**: Not detected as duplicates (constants must be identical)

### After

```python
def foo():
    x = 1
    y = 2
    return x + y

def bar():
    x = 3
    y = 4
    return x + y
```

**New behavior**: ✓ Detected as duplicates!

**Refactored**:
```python
def extracted_func(param_0, param_1):
    x = param_0
    y = param_1
    return x + y

def foo():
    return extracted_func(1, 2)

def bar():
    return extracted_func(3, 4)
```

## Configuration

Constant parameterization is **enabled by default** but can be disabled:

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

# Enable constant parameterization (default)
engine = UnificationRefactorEngine(
    max_parameters=5,
    min_lines=4,
    parameterize_constants=True  # ← Enable
)

# Disable constant parameterization (old behavior)
engine = UnificationRefactorEngine(
    max_parameters=5,
    min_lines=4,
    parameterize_constants=False  # ← Disable
)
```

## Use Cases

This feature enables detection of:

### 1. Different Thresholds
```python
def validate_adult(age):
    if age >= 18:  # ← 18
        return True
    return False

def validate_senior(age):
    if age >= 65:  # ← 65
        return True
    return False
```

Detected! Refactors to `validate_age(age, threshold)`.

### 2. Different Configuration Values
```python
def configure_dev():
    timeout = 30        # ← 30
    max_retries = 3     # ← 3
    return setup(timeout, max_retries)

def configure_prod():
    timeout = 300       # ← 300
    max_retries = 10    # ← 10
    return setup(timeout, max_retries)
```

Detected! Refactors to `configure(timeout_val, retries_val)`.

### 3. Different API Endpoints
```python
def fetch_users():
    url = "/api/v1/users"  # ← "/api/v1/users"
    return http.get(url)

def fetch_posts():
    url = "/api/v1/posts"  # ← "/api/v1/posts"
    return http.get(url)
```

Detected! Refactors to `fetch_resource(endpoint)`.

## Known Limitations

### Loop Variable Names

Currently, the system has a limitation with loop variables that have different names:

```python
def foo():
    for i in range(10):  # ← Loop variable: i
        print(i)

def bar():
    for j in range(10):  # ← Loop variable: j
        print(j)
```

**Issue**: The loop variables `i` and `j` are treated as expressions to parameterize, but they're actually bound variables (alpha-equivalent).

**Current behavior**: Detects as duplicate but generates slightly awkward code.

**Workaround**: Use the same loop variable name in both functions:
```python
def foo():
    for i in range(10):  # ← Same name
        print(i)

def bar():
    for i in range(10):  # ← Same name
        print(i)
```

This will be fixed in a future version with proper alpha-equivalence handling.

## Examples

### Example 1: Simple Constants

**Input**:
```python
def process_small():
    size = 10
    buffer = size * 2
    return buffer

def process_large():
    size = 100
    buffer = size * 2
    return buffer
```

**Output**:
```python
def extracted_func(param_0):
    size = param_0
    buffer = size * 2
    return buffer

def process_small():
    return extracted_func(10)

def process_large():
    return extracted_func(100)
```

### Example 2: Multiple Constants

**Input**:
```python
def calculate_discount_basic(price):
    if price > 100:
        return price * 0.9
    return price

def calculate_discount_premium(price):
    if price > 500:
        return price * 0.8
    return price
```

**Output**:
```python
def extracted_func(param_0, param_1, price):
    if price > param_0:
        return price * param_1
    return price

def calculate_discount_basic(price):
    return extracted_func(100, 0.9, price)

def calculate_discount_premium(price):
    return extracted_func(500, 0.8, price)
```

## Testing

Test constant parameterization:

```bash
# Create test file
cat > /tmp/test_const.py << 'EOF'
def foo():
    x = 1
    y = 2
    return x + y

def bar():
    x = 3
    y = 4
    return x + y
EOF

# Run analyzer
python3 -c "
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(parameterize_constants=True)
proposals = engine.analyze_file('/tmp/test_const.py')

print(f'Found {len(proposals)} proposals')
for p in proposals:
    print(f'  - {p.description}')
"
```

## Implementation Details

### Modified Files

1. **`towel/unification/unifier.py`**
   - Added `parameterize_constants` parameter to `Unifier.__init__()`
   - Modified constant handling in `_unify_nodes()` to parameterize differing constants
   - Added `_unify_for_loop()` for special handling of loop constructs

2. **`towel/unification/refactor_engine.py`**
   - Added `parameterize_constants` parameter to `UnificationRefactorEngine.__init__()`
   - Passes option through to `Unifier`

3. **`towel/unification/extractor.py`**
   - Updated parameter substitution logic to handle constant replacement
   - Fixed f-string handling to preserve structure while replacing constants

### Algorithm

When unifying two blocks:

1. **Compare constants**: If constants differ (e.g., `1` vs `3`)
2. **Check option**: If `parameterize_constants=True`
3. **Parameterize**: Treat the constant as an expression to parameterize
4. **Extract**: Replace with parameter in extracted function
5. **Generate calls**: Pass the original constant values as arguments

## Future Improvements

1. **Alpha-equivalence**: Proper handling of bound variables (loop vars, comprehensions, lambdas)
2. **Smart parameter naming**: Instead of `param_0`, use `threshold`, `size`, etc. based on context
3. **Constant grouping**: Recognize related constants (e.g., min/max pairs)
4. **String similarity**: For string constants, detect similar patterns

## Related Documentation

- `TESTING_BEST_PRACTICES.md` - How to test with constant parameterization
- `OVERLAP_FIX.md` - How overlap filtering works with constants
- `TEST_EXAMPLES_README.md` - Test cases including constant differences
