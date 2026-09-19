# Test Examples - Comprehensive Test Suite

This directory contains comprehensive test examples designed to stress test Towel's unification-based refactoring engine.

## Overview

**Total Examples:** 28 Python files

These examples are intentionally designed to test edge cases, complex patterns,
and challenging scenarios that might break naive duplicate detection systems.

> The per-file "Detected Opportunities" and the summary tallies below are
> *illustrative*: they were recorded against an earlier engine configuration and
> are not a benchmark. The exact proposal count for any fixture changes with the
> engine version and with construction flags such as `skip_trivial_helpers`, so
> treat these numbers as a rough sense of scale, not a contract. What each
> fixture *stresses* is the durable part of this catalog.

## Test Categories

### 1. Basic Examples (Original)

#### `example1_simple.py`
- Simple validation logic duplication
- Basic parameter extraction
- Tests: Straightforward code with minor differences

#### `example2_classes.py`
- Class-based duplication
- Method extraction
- Tests: Object-oriented patterns

#### `example3_file1.py` + `example3_file2.py`
- Cross-file duplication
- Import generation
- Tests: Multi-file refactoring with imports

#### `example4_complex.py`
- Complex data processing loops
- Multiple duplicate patterns
- Tests: Real-world data transformation code

### 2. Binding Constructs

#### `bindings_for_loops.py`
- Alpha-renaming of loop variables (i, j, k)
- Tests: For loop variable equivalence

#### `bindings_comprehensions.py`
- List, dict, set comprehensions
- Generator expressions
- Tests: Comprehension variable scoping

### 3. Return Value Propagation

#### `return_values.py`
- Early returns in blocks
- Nested returns
- Multiple return paths
- Tests: Return value detection at any position

### 4. F-String and Constants

#### `fstrings_constants.py`
- F-string handling (never parameterize literal parts)
- Constant parameterization
- Tests: AST manipulation of format strings

### 5. Scoping Edge Cases

#### `scoping_edge_cases.py`
- Nested functions
- Lambda expressions
- Builtin handling
- Tests: Scope analysis edge cases

---

## New Comprehensive Examples

### 6. Complex Expressions (`complex_expressions.py`)

**Purpose:** Test parameterization of complex sub-expressions

**Patterns Tested:**
- Nested arithmetic expressions: `(x * 2 + 10)` vs `(x * 3 - 5)`
- Complex boolean logic: `(level > 5 and base > 100) or (level > 10 and base > 50)`
- Method chains: `processor.normalize(data["values"]).upper().strip()`
- Nested comprehensions with complex expressions
- Multiple operations in single expression

**Detected Opportunities:** ~14 proposals

**Why It's Challenging:**
- Requires identifying exact sub-expression boundaries
- Must parameterize only the differing parts
- Tests that the unifier doesn't over-parameterize

---

### 7. Hygienic Naming (`hygienic_naming.py`)

**Purpose:** Test name collision avoidance in extracted functions

**Patterns Tested:**
- Parameter names that collide with outer scope variables
- Temporary variables that might shadow parameters
- Nested function definitions with same names
- Multiple parameters with common names (a, b, c, temp, result)

**Detected Opportunities:** ~12 proposals

**Why It's Challenging:**
- Extracted function parameters must not collide with:
  - Variables in calling context
  - Function parameters
  - Local variables in surrounding scope
- Tests hygienic macro-style renaming

**Example:**
```python
def process(data, result):  # 'result' is parameter
    result = []              # 'result' is reassigned
    for item in data:
        x = item * 2         # Block to extract uses 'x'
        ...                  # Extracted function must avoid 'result' collision
```

---

### 8. Referential Transparency (`referential_transparency.py`)

**Purpose:** Test that variable semantics are preserved

**Patterns Tested:**
- Mutable state modifications within blocks
- Side effects (logging, I/O)
- Closures over variables
- Early returns that affect control flow
- Nested scope variable capture
- Multiple external state modifications

**Detected Opportunities:** ~16 proposals

**Why It's Challenging:**
- Must preserve exact semantics of variable references
- Side effects must occur in correct order
- Mutable state modifications must maintain correctness
- Tests referential transparency violations

**Example:**
```python
counter = {"total": 0}
for item in items:
    counter["total"] += item  # Modifies external state
    result = counter["total"] / counter["processed"]
    # Extraction must preserve mutation semantics
```

---

### 9. Nested Structures (`nested_structures.py`)

**Purpose:** Test deep nesting and complex data structures as parameters

**Patterns Tested:**
- Deep dictionary nesting: `item["user"]["profile"]["settings"]["threshold"]`
- Nested comprehensions: `[x ** 2 for x in row if x > threshold]`
- Complex nested structure building
- Nested list operations
- Deep dictionary merging
- Mixed-type processing (strings, numbers, lists)
- Chained nested operations

**Detected Opportunities:** ~18 proposals

**Why It's Challenging:**
- Parameters can be complex nested expressions
- Must track nesting depth correctly
- Tests that the unifier handles complex AST structures

**Example:**
```python
value = item["user"]["profile"]["settings"]["threshold"]  # Deep nesting as parameter
processed = [x ** 2 for x in [y for y in data if y > 0]]  # Nested comprehension
```

---

### 10. Method Chains (`method_chains.py`)

**Purpose:** Test method chaining and fluent interfaces

**Patterns Tested:**
- API response processing: `response.json().get("data", {}).get("items", [])`
- Fluent interface patterns: `model.set_name().set_description().validate()`
- Database query builders
- Stream processing pipelines
- Response builder patterns
- Entity transformations
- Aggregation with method chaining

**Detected Opportunities:** ~16 proposals

**Why It's Challenging:**
- Long method chains must be parameterized correctly
- Fluent interface calls have intermediate return values
- Tests object-oriented programming patterns

**Example:**
```python
results = (db.table("users")
             .where("age", ">", 18)
             .order_by("created_at", "desc")
             .limit(100)
             .get())
# Different limit value becomes parameter
```

---

### 11. Functional Patterns (`functional_patterns.py`)

**Purpose:** Test lambda expressions and functional programming

**Patterns Tested:**
- Lambdas with captured variables: `lambda x: x * multiplier + 10`
- `map`, `filter`, `reduce` patterns
- Higher-order functions that return functions
- Function composition: `h(g(f(x)))`
- Partial application
- Currying patterns
- Generator expressions with lambdas

**Detected Opportunities:** ~18 proposals

**Why It's Challenging:**
- Lambdas create closures over variables
- Higher-order functions have complex types
- Composition chains must be parameterized correctly
- Tests functional programming paradigms

**Example:**
```python
transform = lambda x: x * multiplier + 10  # Captures multiplier
filtered = filter(lambda x: x > 0, data)
result = list(map(transform, filtered))
# Different offset value becomes parameter
```

---

### 12. Real World Patterns (`real_world_patterns.py`)

**Purpose:** Test patterns from actual production code

**Patterns Tested:**
- API request handling (auth, rate limiting, validation)
- ETL pipelines with retry logic
- Business rule validation
- Batch processing with error handling
- Cache-with-fallback patterns
- Time-series metric aggregation

**Detected Opportunities:** ~14 proposals

**Why It's Challenging:**
- Complex multi-step workflows
- Error handling with multiple exception types
- Retry logic with state
- Production-grade patterns with logging and metrics

**Example:**
```python
# API request with auth, rate limiting, validation, logging
if not auth.verify_token(request.headers.get("Authorization")):
    logger.warn("Invalid token")
    return {"error": "Unauthorized"}, 401
# ... complex multi-step flow
```

---

### 13. Edge Cases & Stress Tests (`edge_cases_stress_test.py`)

**Purpose:** Combine multiple challenges to stress test the system

**Patterns Tested:**
- **Deep nesting:** 3+ levels of nested loops
- **Many parameters:** Functions with 8+ parameters in complex expressions
- **Complex control flow:** Multiple if-elif-else branches
- **Mixed comprehensions:** List, dict, set, and nested comprehensions together
- **Exception-heavy code:** Multiple exception types and handlers
- **State machines:** Complex state transition logic

**Detected Opportunities:** ~12 proposals

**Why It's Challenging:**
- Combines multiple edge cases in single examples
- Tests system limits (parameter count, nesting depth)
- Complex control flow graphs
- Real stress test scenarios

**Example:**
```python
# Deep nesting with many variables
for outer in data:
    if outer.get("enabled"):
        for middle in outer.get("children", []):
            if middle.get("status") == "active":
                for inner in middle.get("values", []):
                    computed = (inner["value"] * config["multiplier"] +
                                config["offset"]) ** config["power"]
                    # ... more complexity
```

---


### 14. Adversarial & Comprehensive Fixtures

These fixtures were added by the adversarial review batteries and the syntactic
coverage sweep. They exist to break unsound extraction rather than to showcase
clean opportunities, so many are expected to yield *few or no* proposals — a
correct engine must decline the unsafe ones.

- `binding_constructs_comprehensive.py` / `syntactic_coverage_comprehensive.py` —
  exhaustive sweep of binding forms and surface syntax the extractor must handle.
- `closure_adversarial.py` — closures whose captured names would change meaning if
  hoisted into a helper.
- `control_flow_adversarial.py` — `break`/`continue`/`return`/`else`-clause flow
  that must not be silently relocated.
- `exception_adversarial.py` — `try`/`except`/`finally` and exception-group shapes
  where extraction could reorder handlers.
- `global_nonlocal_examples.py` — `global`/`nonlocal` declarations whose scope must
  survive extraction.
- `side_effects_adversarial.py` — evaluation-order and identity-sensitive effects
  that gate the value/thunk/lifted parameter choice.
- `tricky_edge_cases_adversarial.py` — assorted soundness traps consolidated from
  ecosystem findings.

---

### 15. Annotated, Generator and Async Code

Added by the fifth audit, which found no annotated code, generators, async
functions, decorated classes or keyword-only parameters among the goldens.

- `annotated_module.py` — PEP 526 annotated locals inside the duplicated
  blocks (the helpers keep them and derive their return annotations), annotated
  signatures with keyword-only parameters, a frozen `@dataclass` and absolute
  stdlib imports. Three extractions: two module-level helpers and one method
  helper.
- `generators_async.py` — the same four-line prefix duplicated across two
  generator functions and two `async` functions is extracted once (a helper
  called from all four); the duplicated blocks that contain `yield`
  (`numbered_*`) or `await` (`drain_*`) stay where they are, because a helper
  cannot suspend the caller's frame.

---

## Summary Statistics

| Category | Files |
|----------|-------|
| Basic Examples | 4 |
| Binding/Scoping | 3 |
| Complex Expressions | 1 |
| Hygienic Naming | 1 |
| Referential Transparency | 1 |
| Nested Structures | 1 |
| Method Chains | 1 |
| Functional Patterns | 1 |
| Real World | 1 |
| Edge Cases/Stress | 1 |
| Adversarial & Comprehensive | 7 |
| Annotated, Generator and Async | 2 |
| **TOTAL** | **28** |

## What These Tests Validate

### Unification Algorithm
- ✅ Correctly identifies structural similarity
- ✅ Parameterizes only differing expressions
- ✅ Handles complex nested AST nodes
- ✅ Respects alpha-equivalence of binding constructs

### Hygienic Function Extraction
- ✅ Generates unique parameter names without collisions
- ✅ Preserves variable semantics and scope
- ✅ Maintains referential transparency
- ✅ Handles closure capture correctly

### Parameter Extraction
- ✅ Extracts complex sub-expressions as parameters
- ✅ Handles nested structures (lists, dicts, comprehensions)
- ✅ Works with method chains and fluent interfaces
- ✅ Respects maximum parameter limits

### Scope Analysis
- ✅ Correctly analyzes nested scopes
- ✅ Identifies free variables vs bound variables
- ✅ Handles comprehension variable scoping
- ✅ Detects orphan variables (would-be undefined refs)

### Safety Guarantees
- ✅ Never extracts code that would create orphan variables
- ✅ Propagates return values correctly
- ✅ Preserves side effects and their ordering
- ✅ Maintains exception handling semantics

## Usage

### Preview All Examples
```bash
just preview test_examples/
```

### Preview Specific Category
```bash
just preview test_examples/complex_expressions.py
just preview test_examples/hygienic_naming.py
```

### Apply Refactorings
```bash
# Always to a different directory first!
just dry test_examples/ test_examples_refactored/
```

### Reset After Testing
```bash
just reset-examples
just verify-examples
```

## Maintaining Examples

All example files have pristine templates in `.templates/` directory:
- **Adding new examples:** Create in `test_examples/`, copy to `.templates/`, update justfile
- **Verifying integrity:** Run `just verify-examples`
- **Resetting corrupted files:** Run `just reset-examples`

## Expected Behavior

When running `just preview test_examples/`:

3. **Filtering Rate:** ~23% (removes ~199 overlapping proposals)

This high number of overlapping proposals is expected because:
- The engine extracts ALL possible contiguous sub-blocks
- A 10-line function generates proposals for blocks of length 10, 9, 8, ..., down to min_lines
- Overlap filtering keeps only the largest non-overlapping proposals

The fact that the system handles this gracefully validates the overlap filtering mechanism!
