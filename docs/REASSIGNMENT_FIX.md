# Reassignment Bug and Fix

## Problem Statement

When extracting unified functions from similar code blocks, the system was incorrectly handling reassignment statements on the left-hand side (LHS) of assignments. This caused the extracted function to fail observational equivalence tests.

### Example

Consider these two similar functions:

```python
def conditional_return_a(x, threshold):
    '''Multiple return points.'''
    result = x * 2
    if result > threshold:
        return result
    result = result + 10  # PROBLEM: reassignment
    return result

def conditional_return_b(y, limit):
    '''Similar multiple return pattern.'''
    output = y * 2
    if output > limit:
        return output
    output = output + 10  # PROBLEM: reassignment
    return output
```

When extracting the block containing `result = result + 10`, the system should produce:

```python
# EXPECTED
def extracted_func(__param_0):
    __param_0 = __param_0 + 10
    return __param_0
```

But it was actually producing:

```python
# ACTUAL (BUGGY)
def extracted_func(__param_0):
    result = __param_0 + 10  # BUG: LHS not substituted!
    return result
```

The bug: the RHS occurrence of `result` was correctly replaced with `__param_0`, but the LHS was treated as a fresh binding rather than a reassignment to an existing variable.

## Root Cause

The fundamental issue was **treating reassignments as fresh bindings** when they should be treated as **non-binding variable references**.

In Python's scoping rules:
- `result = x * 2` is a **fresh binding** (introduces a new variable)
- `result = result + 10` is a **reassignment** (LHS references an existing variable)

When extracting a code block:
- If the extracted block includes `result = x * 2`, then `result` is bound within that block
- If the extracted block only includes `result = result + 10`, then `result` is NOT bound within the block - both LHS and RHS are references to an external variable that must be parameterized

The system was always treating assignment LHS as fresh bindings, which is incorrect for reassignments.

### The Deepcopy Challenge

The fix required passing reassignment information from the original AST nodes to the copied nodes used in extraction. This presented a technical challenge:

**Problem**: Python's `copy.deepcopy()` creates new objects with different `id()` values, breaking node-based mappings.

**Failed Approach 1**: Re-analyze reassignments on the copied block
- Doesn't work because the copied block lacks the original context
- Example: `result = result + 10` alone looks like a fresh binding without seeing `result = x * 2` earlier

**Failed Approach 2**: Annotate nodes with `_is_reassignment` attribute before deepcopy
- Custom attributes don't survive `deepcopy()`

**Successful Approach**: Use location-based keys `(line_no, col_offset, target_name)`
- Source location information IS preserved through deepcopy
- Map reassignment status by location before copying
- Re-extract the mapping by location after copying

## The Fix

The fix involved three main changes across the unification pipeline:

### 1. Analyze Reassignments BEFORE Unification

Modified `refactor_engine.py` to analyze both functions for reassignments before attempting unification:

```python
# Analyze both functions for reassignments BEFORE unification
reassignments1 = {}
reassignments2 = {}
if func1:
    reassignments1 = analyze_assignments(func1)
if func2:
    reassignments2 = analyze_assignments(func2)

reassignments_list = [reassignments1, reassignments2]

# Pass reassignments through the pipeline
substitution = self.unifier.unify_blocks(blocks, hygienic_renames, reassignments_list)
```

This follows the critical principle: **Assignment analysis must happen before unification**, because unification needs to know which LHS positions are reassignments vs fresh bindings.

### 2. Thread Reassignment Information Through Unification

Modified `unifier.py` to accept and store the reassignments list:

```python
def unify_blocks(
    self,
    blocks: List[List[ast.AST]],
    hygienic_renames: List[Dict[str, str]],
    reassignments_list: Optional[List[Dict[int, bool]]] = None
) -> Optional[Substitution]:
    # Store reassignments for use during unification
    self.reassignments_list = reassignments_list if reassignments_list else [{} for _ in blocks]
    # ... rest of method
```

### 3. Handle Reassignments in Function Extraction

Modified `extractor.py` with three key improvements:

#### a. Location-based Reassignment Preservation (Lines 85-127)

Before deepcopy, map reassignment status by source location:

```python
# Build a location-based reassignments dict from the original node-based one
location_based_reassignments = {}
class LocationMapper(ast.NodeVisitor):
    def __init__(self, reassignments_dict, location_dict):
        self.reassignments_dict = reassignments_dict
        self.location_dict = location_dict

    def visit_Assign(self, node):
        # Map this assignment by its location and target name
        is_reassignment = self.reassignments_dict.get(id(node), False)
        for target in node.targets:
            if isinstance(target, ast.Name):
                key = (node.lineno, node.col_offset, target.id)
                self.location_dict[key] = is_reassignment
        self.generic_visit(node)

mapper = LocationMapper(reassignments, location_based_reassignments)
for stmt in template_block:
    mapper.visit(stmt)

# Now deepcopy - the source locations will be preserved
copied_block = copy.deepcopy(template_block)

# Create a reassignments dict for the copied block using source locations
reassignments_for_copied_block = {}
class LocationBasedExtractor(ast.NodeVisitor):
    def __init__(self, location_dict, node_dict):
        self.location_dict = location_dict
        self.node_dict = node_dict

    def visit_Assign(self, node):
        # Look up reassignment status by location and target name
        for target in node.targets:
            if isinstance(target, ast.Name):
                key = (node.lineno, node.col_offset, target.id)
                if key in self.location_dict:
                    self.node_dict[id(node)] = self.location_dict[key]
                    break
        self.generic_visit(node)

extractor = LocationBasedExtractor(location_based_reassignments, reassignments_for_copied_block)
for stmt in copied_block:
    extractor.visit(stmt)
```

#### b. Fix Visitor Dispatch (Lines 426-436)

The custom `visit()` method wasn't delegating to `visit_Assign()`:

```python
def visit(self, node):
    # CRITICAL: Delegate to specific visitor methods for nodes that need special handling
    # This must happen BEFORE parameterization checks
    if isinstance(node, ast.Assign):
        return self.visit_Assign(node)
    elif isinstance(node, ast.For):
        return self.visit_For(node)
    elif isinstance(node, ast.comprehension):
        return self.visit_comprehension(node)
    elif isinstance(node, ast.JoinedStr):
        return self.visit_JoinedStr(node)
    # ... rest of method
```

Without this, `visit_Assign()` was never being called, so reassignment handling never occurred.

#### c. Implement Reassignment-Aware visit_Assign (Lines 359-410)

Handle reassignment LHS correctly:

```python
def visit_Assign(self, node):
    # Transform the value expression first
    new_value = self.visit(node.value)

    # Check if this assignment was a reassignment in the original function
    is_reassignment_in_original = self.reassignments.get(id(node), False)

    # Check if we're assigning a parameter to a variable (e.g., result = __param_0)
    if isinstance(new_value, ast.Name) and new_value.id in self.param_names:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.var_to_param[target.id] = new_value.id

    # Transform targets: substitute if the variable is mapped to a parameter
    new_targets = []
    for target in node.targets:
        if isinstance(target, ast.Name):
            renamed_id = self.rename_mapping.get(target.id, target.id)

            if is_reassignment_in_original and renamed_id in self.param_names:
                # Reassignment to a free variable that's now a parameter
                new_targets.append(ast.Name(id=renamed_id, ctx=ast.Store()))
            elif is_reassignment_in_original and target.id in self.var_to_param:
                # Reassignment to a parameterized variable - substitute LHS
                param_name = self.var_to_param[target.id]
                new_targets.append(ast.Name(id=param_name, ctx=ast.Store()))
            elif target.id in self.var_to_param and not is_reassignment_in_original:
                param_name = self.var_to_param[target.id]
                new_targets.append(ast.Name(id=param_name, ctx=ast.Store()))
            else:
                # New binding or not parameterized - keep as is
                new_targets.append(target)
        else:
            # Complex target (e.g., tuple unpacking, subscript)
            # Visit recursively to transform any Name nodes inside
            new_targets.append(self.visit(target))

    return ast.Assign(targets=new_targets, value=new_value)
```

Key logic:
1. Check if this assignment was a reassignment in the original function
2. For reassignment LHS that maps to a parameter, substitute the LHS with the parameter name
3. For complex targets (Subscript, tuple unpacking), recursively visit to transform nested Name nodes

### 4. Handle Complex Assignment Targets

An initial implementation broke dict subscript assignments like `result[key] = value`. The problem was treating complex targets (non-Name nodes) as atomic units instead of recursively transforming them.

**Bug**: `result[key] = value` became `result[key] = __param_0`, causing NameError on `result`

**Fix**: Recursively visit complex targets to transform any Name nodes inside:

```python
else:
    # Complex target (e.g., tuple unpacking, subscript)
    # Visit recursively to transform any Name nodes inside
    new_targets.append(self.visit(target))  # Recursively transform!
```

This ensures that for `result[key]` (a Subscript node), we visit it and transform the `result` Name node to `__param_0`, producing `__param_0[key] = value`.

## Results

After implementing the fix, all three test proposals in `tricky_edge_cases_adversarial.py` pass observational equivalence tests:

- **Proposal 1 (conditional_return)**: Tests reassignment with early returns
- **Proposal 2 (list operations)**: Tests list subscript reassignments
- **Proposal 3 (dict operations)**: Tests dict subscript reassignments

Example of correct extraction for Proposal 1:

```python
# Original functions
def conditional_return_a(x, threshold):
    result = x * 2
    if result > threshold:
        return result
    result = result + 10  # Extracted block starts here
    return result

def conditional_return_b(y, limit):
    output = y * 2
    if output > limit:
        return output
    output = output + 10  # Extracted block starts here
    return output

# Correctly extracted function
def extracted_func(__param_0):
    __param_0 = __param_0 + 10  # Both LHS and RHS substituted!
    return __param_0

# Refactored originals
def conditional_return_a(x, threshold):
    result = x * 2
    if result > threshold:
        return result
    result = extracted_func(result)
    return result

def conditional_return_b(y, limit):
    output = y * 2
    if output > limit:
        return output
    output = extracted_func(output)
    return output
```

The extracted function is observationally equivalent to the original code blocks.

## Key Insights

1. **Reassignments are not bindings**: When a variable is reassigned in an extracted block but initially bound outside that block, the LHS must be treated as a non-binding reference.

2. **Timing matters**: Assignment analysis must happen BEFORE unification. The unification algorithm needs to know which assignments are reassignments to correctly match code patterns.

3. **Deepcopy breaks node identity**: Standard approaches using `id(node)` don't work across deepcopy boundaries. Location-based identification survives deepcopy.

4. **Visitor pattern requires explicit dispatch**: Custom `visit()` methods must explicitly delegate to specific visitor methods like `visit_Assign()` - the default `generic_visit()` only visits children.

5. **Complex targets need recursive transformation**: Assignment targets like `result[key]` or `(a, b)` contain Name nodes that must be recursively transformed.

## Files Modified

- `src/towel/unification/refactor_engine.py`: Lines 451-468, 589
- `src/towel/unification/unifier.py`: Lines 435-466
- `src/towel/unification/extractor.py`: Lines 77-135, 359-410, 426-436

## Test Coverage

The fix is validated by:
- `test_examples/tricky_edge_cases_adversarial.py`: Contains the original failing test case
- Observational equivalence testing: Verifies the extracted function produces identical behavior to the original code

All proposals now pass automatic observational equivalence tests.
