# Towel - Usage Guide

> **Supplementary API guide.** A how-to for the `UnificationRefactorEngine` API. The maintained overview and CLI workflow are in the [README](../README.md); current limitations are in [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Quick Start

There are three ways to use Towel, the unification-based code refactoring tool:

### 1. Analyze a Single File

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# Analyze one file
proposals = engine.analyze_file("myfile.py")

# Apply best proposal
if proposals:
    refactored = engine.apply_refactoring("myfile.py", proposals[0])
    with open("myfile.py", 'w') as f:
        f.write(refactored)
```

### 2. Analyze Multiple Files (for cross-file duplicates)

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# Analyze multiple files together
files = ["file1.py", "file2.py", "file3.py"]
proposals = engine.analyze_files(files)

# Apply best proposal (handles cross-file)
if proposals:
    modified_files = engine.apply_refactoring_multi_file(proposals[0])

    # Write all modified files
    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)
```

### 3. Analyze an Entire Directory

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)

# Analyze all Python files in a directory (recursive)
proposals = engine.analyze_directory("src/", recursive=True)

# Apply all proposals
for proposal in proposals:
    modified_files = engine.apply_refactoring_multi_file(proposal)

    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)

## Progress Modes & Termination Reason

When using the fixed-point directory refactoring loop (`refactor_directory_to_fixed_point`) or the `dry` CLI, you can control progress output:

| Mode    | Behavior |
|---------|----------|
| `tqdm`  | Rich progress bar showing applied count & queue length. |
| `auto`  | Attempts `tqdm`, falls back to a textual single-line bar. |
| `none`  | Suppresses all progress output (quiet for CI). |
| `detail`| Verbose listing of discovered proposals (first 25) and localized follow-ups after each application. |

Call signature returns `(results_dict, termination_reason)` where `termination_reason` is:

* `fixed_point` – No further proposals remain.
* `iteration_cap` – Stopped because `max_iterations` limit was reached.

Set `max_iterations=0` for unlimited iterations until a fixed point.

### Example (detail mode)

```python
engine = UnificationRefactorEngine()
results, reason = engine.refactor_directory_to_fixed_point(
    "my_project", "my_project_out", max_iterations=0, progress="detail"
)
print("Termination:", reason)
```

### Localized Follow-Ups

After each applied proposal, the engine re-analyzes only the changed files to enqueue *localized* follow-up proposals immediately. This accelerates chained extractions without rescanning the entire project every iteration.
```

## Command-Line Usage

Use the provided script to analyze a directory:

```bash
python3 analyze_directory.py test_examples
```

This will:
1. Find all Python files in the directory
2. Analyze them for duplicates (including cross-file)
3. Show all refactoring opportunities
4. Ask if you want to apply them
5. Write the refactored code

## Configuration

### Engine Parameters

```python
engine = UnificationRefactorEngine(
    max_parameters=5,  # Maximum parameters for extracted functions
                       # (prevents over-parameterization)

    min_lines=4        # Minimum lines for a code block
                       # (smaller blocks are ignored)
)
```

### Directory Scanning

```python
# Recursive (default) - searches subdirectories
proposals = engine.analyze_directory("src/", recursive=True)

# Non-recursive - only files in the directory itself
proposals = engine.analyze_directory("src/", recursive=False)
```

The scanner automatically skips:
- Hidden directories (starting with `.`)
- `__pycache__`
- `venv`, `env`
- `node_modules`

## Understanding Proposals

Each proposal contains:

```python
proposal.description          # "Extract common code from func1 and func2"
proposal.parameters_count     # Number of parameters in extracted function
proposal.extracted_function   # The AST of the new function
proposal.replacements         # List of (line_range, call_node, file_path)
proposal.file_path            # Canonical location for the extracted function
```

### Cross-File vs Same-File

```python
# Check if it's cross-file
is_cross_file = any(len(r) == 3 for r in proposal.replacements)

if is_cross_file:
    # Multiple files affected - use multi-file method
    modified_files = engine.apply_refactoring_multi_file(proposal)
else:
    # Single file - simpler method works
    refactored = engine.apply_refactoring(proposal.file_path, proposal)
```

## Examples

### Example 1: Simple Validation Code

**Before:**
```python
def process_user_data(user_id):
    user = {"id": user_id, "name": "John"}
    if not user.get("id"):
        raise ValueError("User ID is required")
    if not user.get("name"):
        raise ValueError("User name is required")
    if len(user.get("name", "")) < 2:
        raise ValueError("User name too short")
    return user

def process_admin_data(admin_id):
    admin = {"id": admin_id, "name": "Jane", "role": "admin"}
    if not admin.get("id"):
        raise ValueError("User ID is required")
    if not admin.get("name"):
        raise ValueError("User name is required")
    if len(admin.get("name", "")) < 2:
        raise ValueError("User name too short")
    return admin
```

**After:**
```python
def extracted_func(param_9):
    if not param_9.get('id'):
        raise ValueError('User ID is required')
    if not param_9.get('name'):
        raise ValueError('User name is required')
    if len(param_9.get('name', '')) < 2:
        raise ValueError('User name too short')

def process_user_data(user_id):
    user = {"id": user_id, "name": "John"}
    extracted_func(user)
    return user

def process_admin_data(admin_id):
    admin = {"id": admin_id, "name": "Jane", "role": "admin"}
    extracted_func(admin)
    return admin
```

### Example 2: Cross-File Duplicates

**Before (file1.py):**
```python
def calculate_discount_for_regular_customer(price, customer):
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price
```

**Before (file2.py):**
```python
def calculate_discount_for_premium_customer(price, customer):
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price
```

**After (file1.py):**
```python
def extracted_func(customer, price):
    base_discount = 0.1
    if customer.get('years_member', 0) > 5:
        base_discount += 0.05
    if customer.get('total_purchases', 0) > 1000:
        base_discount += 0.05
    discount_amount = price * base_discount
    final_price = price - discount_amount
    return final_price

def calculate_discount_for_regular_customer(price, customer):
    return extracted_func(customer, price)
```

**After (file2.py):**
```python
from file1 import extracted_func

def calculate_discount_for_premium_customer(price, customer):
    return extracted_func(customer, price)
```

## Tips

### 1. Always Use analyze_files() or analyze_directory() for Cross-File

```python
# ❌ BAD - Misses cross-file duplicates
proposals1 = engine.analyze_file("file1.py")
proposals2 = engine.analyze_file("file2.py")

# ✅ GOOD - Finds cross-file duplicates
proposals = engine.analyze_files(["file1.py", "file2.py"])
```

### 2. Review Proposals Before Applying

```python
proposals = engine.analyze_directory("src/")

# Review each proposal
for i, prop in enumerate(proposals, 1):
    print(f"{i}. {prop.description}")
    print(f"   Parameters: {prop.parameters_count}")
    print(f"   Function: {prop.extracted_function.name}")
    print()

# Selectively apply
best_proposal = proposals[0]
```

### 3. Iterative Refactoring

Running the tool multiple times can find more opportunities:

```python
# First pass
proposals = engine.analyze_directory("src/")
# Apply proposals...

# Second pass - may find more after first refactoring
proposals2 = engine.analyze_directory("src/")
```

### 4. Filter by Parameters

```python
# Only apply refactorings with few parameters (cleaner)
simple_proposals = [p for p in proposals if p.parameters_count <= 2]
```

## Troubleshooting

### "No proposals found"

- Check `min_lines` - code blocks might be too small
- Check `max_parameters` - duplicates might differ in too many ways
- Ensure constants match (tool doesn't parameterize different constants)

### "Too many proposals"

- Increase `min_lines` to focus on larger duplicates
- Decrease `max_parameters` to avoid over-parameterized functions
- Increase structural similarity threshold in code

### Import errors after refactoring

- Make sure you're using `analyze_files()` or `analyze_directory()`
- Check that module names are correct (based on file names)
- Consider using relative imports for packages

## Fast local testing

When developing or iterating on changes, run the fast smoke suite to validate core behavior and output stability without the cost of the full test run:

```
just test-smoke
```

Use this during inner-loop development; reserve the full suite for pre-commit or release gating.

## Advanced Usage

### Custom Filtering

```python
def is_good_proposal(proposal):
    """Filter criteria for good refactorings."""
    # Must have few parameters
    if proposal.parameters_count > 3:
        return False

    # Must save significant code
    lines_saved = sum(r[0][1] - r[0][0] for r in proposal.replacements)
    if lines_saved < 10:
        return False

    return True

good_proposals = [p for p in proposals if is_good_proposal(p)]
```

### Dry Run

```python
# Preview without writing
for proposal in proposals:
    print(f"\nProposal: {proposal.description}")
    print("Extracted function:")
    print(ast.unparse(proposal.extracted_function))
    print()
```

### Selective Application

```python
# Only apply to specific files
for proposal in proposals:
    if "utils" in proposal.file_path:
        modified_files = engine.apply_refactoring_multi_file(proposal)
        # Write files...
```
