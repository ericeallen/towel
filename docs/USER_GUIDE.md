# Towel User Guide: Common Refactoring Patterns

## Table of Contents

1. [Getting Started](#getting-started)
2. [Basic Usage](#basic-usage)
3. [Common Refactoring Patterns](#common-refactoring-patterns)
4. [Advanced Scenarios](#advanced-scenarios)
5. [Troubleshooting](#troubleshooting)
6. [Best Practices](#best-practices)

---

## Getting Started

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/towel.git
cd towel

# Install in development mode
pip install -e ".[dev]"

# Set up pre-commit hooks (optional)
pre-commit install
```

### Quick Start

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

# Create engine instance
engine = UnificationRefactorEngine()

# Analyze a single file
proposals = engine.analyze_file("my_code.py")

# Analyze multiple files
proposals = engine.analyze_files(["file1.py", "file2.py"])

# Analyze entire directory
proposals = engine.analyze_directory("src/", recursive=True)

# Print proposals
for i, proposal in enumerate(proposals):
    print(f"Proposal {i}: {proposal.description}")
```

---

## Basic Usage

### Analyzing Files

#### Single File Analysis

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(
    min_lines=4,      # Minimum duplicate size
    max_lines=15,     # Maximum block size to extract
    max_parameters=5  # Maximum parameters for extracted function
)

proposals = engine.analyze_file("app.py")
```

#### Multi-File Analysis

```python
files = [
    "src/auth.py",
    "src/database.py",
    "src/api.py"
]

proposals = engine.analyze_files(files, verbose=True, progress="auto")
```

#### Directory Analysis

```python
proposals = engine.analyze_directory(
    "src/",
    recursive=True,      # Include subdirectories
    verbose=True,        # Print analysis details
    progress="tqdm"      # Show progress bar
)
```

### Using the Pipeline API

```python
from towel.unification.pipeline import run_pipeline

# Higher-level API with caching and progress
proposals = run_pipeline(
    ["file1.py", "file2.py"],
    progress="auto",                    # Progress display
    invalidate_paths=["file1.py"]       # Re-analyze only changed files
)
```

---

## Common Refactoring Patterns

### Pattern 1: Duplicate Validation Logic

**Before**:
```python
# file1.py
def process_user(user):
    if not user.get('id'):
        raise ValueError('User ID required')
    if not user.get('email'):
        raise ValueError('Email required')
    if '@' not in user.get('email', ''):
        raise ValueError('Invalid email')
    # ... process user

# file2.py
def update_user(user):
    if not user.get('id'):
        raise ValueError('User ID required')
    if not user.get('email'):
        raise ValueError('Email required')
    if '@' not in user.get('email', ''):
        raise ValueError('Invalid email')
    # ... update user
```

**After Refactoring**:
```python
# shared.py
def validate_user(user):
    """Validate user object has required fields."""
    if not user.get('id'):
        raise ValueError('User ID required')
    if not user.get('email'):
        raise ValueError('Email required')
    if '@' not in user.get('email', ''):
        raise ValueError('Invalid email')

# file1.py
from shared import validate_user

def process_user(user):
    validate_user(user)
    # ... process user

# file2.py
from shared import validate_user

def update_user(user):
    validate_user(user)
    # ... update user
```

**How to Apply**:
```python
engine = UnificationRefactorEngine(min_lines=3)
proposals = engine.analyze_files(["file1.py", "file2.py"])

# Towel will detect the duplicate validation logic and propose extraction
for proposal in proposals:
    if "validate" in proposal.description.lower():
        print(f"Found validation duplication: {proposal.description}")
```

---

### Pattern 2: Repeated Calculation Logic

**Before**:
```python
def calculate_discount_regular(price, customer):
    discount = 0.1
    if customer.get('years', 0) > 5:
        discount += 0.05
    if customer.get('total_purchases', 0) > 1000:
        discount += 0.05
    return price * (1 - discount)

def calculate_discount_premium(price, customer):
    discount = 0.1
    if customer.get('years', 0) > 5:
        discount += 0.05
    if customer.get('total_purchases', 0) > 1000:
        discount += 0.05
    return price * (1 - discount)
```

**After Refactoring**:
```python
def __extracted_discount_calculation(price, customer):
    """Calculate discount based on customer loyalty."""
    discount = 0.1
    if customer.get('years', 0) > 5:
        discount += 0.05
    if customer.get('total_purchases', 0) > 1000:
        discount += 0.05
    return price * (1 - discount)

def calculate_discount_regular(price, customer):
    return __extracted_discount_calculation(price, customer)

def calculate_discount_premium(price, customer):
    return __extracted_discount_calculation(price, customer)
```

**Detection**:
```python
engine = UnificationRefactorEngine(min_lines=4)
proposals = engine.analyze_file("pricing.py")

for proposal in proposals:
    print(f"Parameters: {proposal.parameters_count}")
    print(f"Return values: {proposal.return_variables}")
```

---

### Pattern 3: Configuration Parsing

**Before**:
```python
# service1.py
def load_config():
    config = read_file('config.json')
    if not config:
        config = {}
    config.setdefault('timeout', 30)
    config.setdefault('retries', 3)
    return config

# service2.py
def init_service():
    settings = read_file('config.json')
    if not settings:
        settings = {}
    settings.setdefault('timeout', 30)
    settings.setdefault('retries', 3)
    return settings
```

**After Refactoring**:
```python
def __load_config_with_defaults(config_dict):
    """Load configuration with default values."""
    if not config_dict:
        config_dict = {}
    config_dict.setdefault('timeout', 30)
    config_dict.setdefault('retries', 3)
    return config_dict

# service1.py
def load_config():
    config = read_file('config.json')
    return __load_config_with_defaults(config)

# service2.py
def init_service():
    settings = read_file('config.json')
    return __load_config_with_defaults(settings)
```

---

### Pattern 4: Loop-Based Data Transformation

**Before**:
```python
def process_items_a(items):
    result = []
    for item in items:
        if item.get('active'):
            result.append(item['id'])
    return result

def process_items_b(values):
    output = []
    for value in values:
        if value.get('active'):
            output.append(value['id'])
    return output
```

**After Refactoring**:
```python
def __extract_active_ids(__param_items):
    """Extract IDs from active items."""
    result = []
    for item in __param_items:
        if item.get('active'):
            result.append(item['id'])
    return result

def process_items_a(items):
    return __extract_active_ids(items)

def process_items_b(values):
    return __extract_active_ids(values)
```

**Key Insight**: Towel unifies variables nominally, so loop variables (`item`, `value`) are recognized as playing the same role.

---

### Pattern 5: Error Handling Boilerplate

**Before**:
```python
def fetch_user(user_id):
    try:
        user = api.get_user(user_id)
        log.info(f"Fetched user {user_id}")
        return user
    except APIError as e:
        log.error(f"Failed to fetch user: {e}")
        return None

def fetch_product(product_id):
    try:
        product = api.get_product(product_id)
        log.info(f"Fetched product {product_id}")
        return product
    except APIError as e:
        log.error(f"Failed to fetch product: {e}")
        return None
```

**After Refactoring**:
```python
def __api_fetch_with_logging(fetch_fn, entity_id, entity_name):
    """Fetch entity with error handling and logging."""
    try:
        result = fetch_fn(entity_id)
        log.info(f"Fetched {entity_name} {entity_id}")
        return result
    except APIError as e:
        log.error(f"Failed to fetch {entity_name}: {e}")
        return None

def fetch_user(user_id):
    return __api_fetch_with_logging(api.get_user, user_id, "user")

def fetch_product(product_id):
    return __api_fetch_with_logging(api.get_product, product_id, "product")
```

**Note**: This may require manual adjustment to make `fetch_fn` a parameter.

---

## Advanced Scenarios

### Cross-File Refactoring

**Setup**:
```
project/
  ├── services/
  │   ├── user_service.py
  │   └── admin_service.py
  └── shared/
      └── validation.py  (extracted code goes here)
```

**Analysis**:
```python
files = [
    "services/user_service.py",
    "services/admin_service.py"
]

proposals = engine.analyze_files(files)

# Cross-file proposals will include import statements
for proposal in proposals:
    file_paths = {r.file_path for r in proposal.replacements}
    if len(file_paths) > 1:
        print(f"Cross-file proposal: {proposal.description}")
        print(f"Affected files: {file_paths}")
```

### Method Extraction (Class Context)

**Before**:
```python
class UserProcessor:
    def process(self):
        data = self.fetch_data()
        if not data:
            return None
        data = self.clean_data(data)
        return data

class AdminProcessor:
    def process(self):
        data = self.fetch_data()
        if not data:
            return None
        data = self.clean_data(data)
        return data
```

**After Refactoring**:
```python
# Towel can extract to a base class if hierarchy is detected
class BaseProcessor:
    def __process_with_validation(self):
        """Process data with validation."""
        data = self.fetch_data()
        if not data:
            return None
        data = self.clean_data(data)
        return data

class UserProcessor(BaseProcessor):
    def process(self):
        return self.__process_with_validation()

class AdminProcessor(BaseProcessor):
    def process(self):
        return self.__process_with_validation()
```

**Detection**:
```python
# Enable class hierarchy detection
proposals = engine.analyze_files(["processors.py"])

for proposal in proposals:
    if proposal.insert_into_class:
        print(f"Extract into class: {proposal.insert_into_class}")
        print(f"Method kind: {proposal.method_kind}")
```

### Iterative Refactoring

Apply refactorings one at a time, re-analyzing after each:

```python
def iterative_refactor(file_path, max_iterations=10):
    """Apply refactorings iteratively until no more found."""
    for i in range(max_iterations):
        # Re-analyze with cache invalidation
        proposals = run_pipeline(
            [file_path],
            invalidate_paths=[file_path]
        )

        if not proposals:
            print(f"Converged after {i} iterations")
            break

        # Apply first proposal (manual or automated)
        print(f"Iteration {i}: {len(proposals)} proposals found")

        # TODO: Apply proposal to file
        # engine.apply_proposal(proposals[0], file_path)
```

---

## Troubleshooting

### No Proposals Found

**Possible Reasons**:
1. Code blocks too small (increase `min_lines`)
2. Code blocks too large (increase `max_lines`)
3. Too many parameters needed (increase `max_parameters`)
4. Variables would be orphaned (scope safety prevents extraction)

**Solutions**:
```python
# Relax constraints
engine = UnificationRefactorEngine(
    min_lines=3,        # Lower minimum
    max_lines=20,       # Higher maximum
    max_parameters=10   # Allow more parameters
)
```

### Orphaned Variable Errors

**Symptom**: Proposals rejected with "OrphanVariableError"

**Cause**: Extraction would separate variable definition from usage

**Example**:
```python
def func1():
    x = compute()
    y = x + 5  # Cannot extract this alone - 'x' defined above

def func2():
    y = ??? + 5  # 'x' not available here - would be orphaned
```

**Solution**: Expand block to include variable definition or accept it as non-extractable

### Too Many Parameters

**Symptom**: "Too many free variables" in verbose output

**Cause**: Extracted function would need many parameters

**Example**:
```python
# 7 free variables - may exceed max_parameters
result = (a + b) * (c + d) - (e + f) / g
```

**Solution**: Increase `max_parameters` or refactor to reduce dependencies

### Unification Failures

**Symptom**: "Cannot unify blocks" in verbose output

**Cause**: Blocks have different structure or binding patterns

**Example**:
```python
# These won't unify due to different binding names
for x in items:   # binds 'x'
    process(x)

for y in items:   # binds 'y' - different!
    process(y)
```

**Solution**: Rename variables to match before analysis, or accept as non-unifiable

---

## Best Practices

### 1. Start with Conservative Settings

```python
# Good starting point
engine = UnificationRefactorEngine(
    min_lines=5,        # Only larger duplications
    max_lines=12,       # Moderate function size
    max_parameters=4    # Keep interfaces simple
)
```

### 2. Use Verbose Mode for Debugging

```python
proposals = engine.analyze_files(files, verbose=True)
# Output shows:
# - Unification attempts
# - Failures with reasons
# - Orphan detection results
```

### 3. Enable Progress for Large Projects

```python
proposals = run_pipeline(
    large_file_list,
    progress="tqdm",     # Visual progress bar
    verbose=False        # Reduce noise
)
```

### 4. Invalidate Only Changed Files

```python
# Initial analysis
run_pipeline(all_files)

# After editing file1.py
run_pipeline(
    all_files,
    invalidate_paths=["file1.py"]  # Re-analyze only this file
)
```

### 5. Review Proposals Before Applying

```python
for i, proposal in enumerate(proposals):
    print(f"\nProposal {i}:")
    print(f"  Description: {proposal.description}")
    print(f"  Parameters: {proposal.parameters_count}")
    print(f"  Replacements: {len(proposal.replacements)}")
    print(f"  Affected files: {set(r.file_path for r in proposal.replacements)}")

    # Inspect extracted function
    import ast
    print(f"  Extracted function: {ast.unparse(proposal.extracted_function)}")
```

### 6. Test Refactored Code

Always run tests after applying refactorings:

```bash
# Run observational equivalence tests
python tests/test_observational_equivalence.py

# Run full test suite
pytest tests/
```

### 7. Use Version Control

```bash
# Commit before refactoring
git commit -am "Before refactoring"

# Apply refactorings
# ...

# Review changes
git diff

# Commit or revert
git commit -am "Applied Towel refactorings" # or: git reset --hard
```

---

## Example Workflows

### Workflow 1: Clean Up Single File

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

# 1. Analyze file
engine = UnificationRefactorEngine(min_lines=4)
proposals = engine.analyze_file("my_module.py", verbose=True)

# 2. Review proposals
print(f"Found {len(proposals)} refactoring opportunities")
for i, p in enumerate(proposals):
    print(f"{i}: {p.description}")

# 3. Select and apply (manual for now)
# TODO: engine.apply_proposal(proposals[0], "my_module.py")
```

### Workflow 2: Cross-File Deduplication

```python
from towel.unification.pipeline import run_pipeline

# 1. Collect all project files
import glob
files = glob.glob("src/**/*.py", recursive=True)

# 2. Run pipeline
proposals = run_pipeline(files, progress="auto", verbose=False)

# 3. Filter cross-file proposals
cross_file = [
    p for p in proposals
    if len(set(r.file_path for r in p.replacements)) > 1
]

print(f"Found {len(cross_file)} cross-file duplications")
```

### Workflow 3: Continuous Refactoring

```python
def continuous_refactor(directory):
    """Continuously find and apply refactorings until convergence."""
    import glob

    iteration = 0
    while True:
        files = glob.glob(f"{directory}/**/*.py", recursive=True)
        proposals = run_pipeline(files, progress="none")

        if not proposals:
            print(f"Converged after {iteration} iterations")
            break

        print(f"Iteration {iteration}: {len(proposals)} proposals")

        # Apply first proposal (placeholder)
        # apply_first_proposal(proposals[0])

        iteration += 1

# Run it
continuous_refactor("src/")
```

---

## Configuration Examples

### Aggressive Extraction

```python
engine = UnificationRefactorEngine(
    min_lines=3,        # Extract even small blocks
    max_lines=25,       # Allow large extractions
    max_parameters=8    # Accept complex signatures
)
```

### Conservative Extraction

```python
engine = UnificationRefactorEngine(
    min_lines=7,        # Only significant duplications
    max_lines=10,       # Keep functions small
    max_parameters=3    # Simple interfaces only
)
```

### Class Method Extraction

```python
# Towel automatically detects class context
engine = UnificationRefactorEngine()
proposals = engine.analyze_file("classes.py")

# Filter for method extractions
method_proposals = [
    p for p in proposals
    if p.insert_into_class is not None
]
```

---

## Integration with Development Tools

### Pre-Commit Hook

Create `.git/hooks/pre-commit`:
```bash
#!/bin/bash
# Check for new refactoring opportunities before commit
python -c "
from towel.unification.refactor_engine import UnificationRefactorEngine
import sys

engine = UnificationRefactorEngine()
proposals = engine.analyze_directory('src/', recursive=True)

if proposals:
    print(f'Warning: {len(proposals)} refactoring opportunities found')
    print('Run Towel to clean up duplications')
    # sys.exit(1)  # Uncomment to block commit
"
```

### CI/CD Integration

```yaml
# .github/workflows/refactoring-check.yml
name: Refactoring Check
on: [pull_request]
jobs:
  check-duplications:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run Towel
        run: |
          pip install -e .
          python scripts/check_duplications.py
```

---

## Getting Help

- **Documentation**: `docs/ARCHITECTURE.md` for technical details
- **Issues**: https://github.com/yourusername/towel/issues
- **Tests**: See `tests/` for usage examples
- **Examples**: See `test_examples/` for sample inputs

---

## Summary

Towel automates the detection and extraction of duplicate code using unification-based analysis. Key points:

1. **Start conservatively** with `min_lines=5`, `max_parameters=4`
2. **Use verbose mode** to understand why proposals are rejected
3. **Test thoroughly** after applying refactorings
4. **Iterate gradually** - apply one proposal at a time for safety
5. **Leverage cross-file analysis** to eliminate duplications across modules

Happy refactoring!
