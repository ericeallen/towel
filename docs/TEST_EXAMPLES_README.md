# Test Examples

This directory contains example Python files for testing the DRY detector.

## Files

### example1_simple.py
**Purpose**: Test same-file duplicate detection in simple functions

**Scenario**: Three functions (`process_user_data`, `process_admin_data`, `process_guest_data`) that all contain identical validation logic.

**Expected behavior**: The detector should identify the duplicate validation code and extract it to a separate function.

**Duplicated code**:
```python
if not user.get("id"):
    raise ValueError("User ID is required")
if not user.get("name"):
    raise ValueError("User name is required")
if len(user.get("name", "")) < 2:
    raise ValueError("User name too short")
```

### example2_classes.py
**Purpose**: Test duplicate detection in class methods

**Scenario**: Three classes (`EmailProcessor`, `SMSProcessor`, `PushNotificationProcessor`) with identical validation logic in their `process()` methods.

**Expected behavior**: The detector should identify the duplicate validation code across class methods.

**Duplicated code**:
```python
if not self.email:
    raise ValueError("Email is required")
if "@" not in self.email:
    raise ValueError("Invalid email format")
if len(self.email) < 5:
    raise ValueError("Email too short")
```

### example3_file1.py & example3_file2.py
**Purpose**: Test cross-file duplicate detection

**Scenario**: Two files with functions that contain identical discount calculation logic.

**Expected behavior**:
- Detector identifies duplicates across files
- Extracts function to `example3_file1.py` (canonical location)
- Adds `from example3_file1 import extracted_func` to `example3_file2.py`
- Passes free variables (`customer`, `price`) as parameters

**Duplicated code**:
```python
base_discount = 0.1
if customer.get("years_member", 0) > 5:
    base_discount += 0.05
if customer.get("total_purchases", 0) > 1000:
    base_discount += 0.05

discount_amount = price * base_discount
final_price = price - discount_amount
```

### example4_complex.py
**Purpose**: Test duplicate detection with loops and complex logic

**Scenario**: Three functions (`process_json_data`, `process_xml_data`, `process_csv_data`) with identical data processing loops.

**Expected behavior**: The detector should identify the duplicate loop logic and extract it.

**Duplicated code**:
```python
result = {}
for key, value in data.items():
    if isinstance(value, str):
        result[key] = value.strip().lower()
    elif isinstance(value, (int, float)):
        result[key] = value * 2
    elif isinstance(value, list):
        result[key] = [str(item) for item in value]
    else:
        result[key] = str(value)
return result
```

## Maintaining Test Files

### Original Templates

All original (clean) versions are stored in `.templates/`:
```
.templates/
├── example1_simple.py
├── example2_classes.py
├── example3_file1.py
├── example3_file2.py
└── example4_complex.py
```

### Resetting Test Files

If test files get modified or corrupted:

```bash
# Reset all test examples to original state
just reset-examples
```

This copies the clean templates from `.templates/` to `test_examples/`.

### Important: Do Not Modify Directly

**Never run refactoring scripts directly on these test files** unless you're specifically testing the user-facing commands.

For automated tests, always use temporary copies:
```python
from test_helpers import temporary_test_file

with temporary_test_file("test_examples/example1_simple.py") as temp_file:
    # Work on temp_file
    # Original remains pristine
```

See `TESTING_BEST_PRACTICES.md` for detailed guidance.

## Testing Coverage

| Test | Purpose | Files Used |
|------|---------|-----------|
| `test_unification_final.py` | Same-file refactoring | example1, example4 |
| `test_cross_file_final.py` | Cross-file refactoring | example3_file1, example3_file2 |
| `test_with_temp_files.py` | Safe testing practices | All examples |
| `simple_example.py` | Preview (read-only) | All examples |
| `analyze_directory.py` | Full refactoring (interactive) | User-specified |

## Verifying Test Files

To verify test files are in their original state:

```bash
# Compare with templates
diff test_examples/example1_simple.py .templates/example1_simple.py

# Or reset and run tests
just reset-examples
just test
```

## Adding New Test Examples

1. Create the example file with clear duplicate code
2. Add documentation comment at top explaining what it tests
3. Save to both:
   - `test_examples/exampleN_*.py` (working copy)
   - `.templates/exampleN_*.py` (clean template)
4. Update `justfile` reset-examples command to include new file
5. Add test case to relevant test files

Example:
```bash
# Create new example
cat > test_examples/example5_new_feature.py << 'EOF'
"""
Example 5: Test new feature XYZ.
"""
# ... code with duplicates ...
EOF

# Save template
cp test_examples/example5_new_feature.py .templates/example5_new_feature.py

# Update justfile
# Add: @cp .templates/example5_new_feature.py test_examples/example5_new_feature.py
```
