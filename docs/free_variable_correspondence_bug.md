# Free Variable Correspondence Bug

## Problem Statement

When unifying code blocks with structurally identical code but different FREE variable names, the generated function calls use incorrect variable names.

## Example

```python
# Block 0 - in process_user_data()
if not user.get("id"):        # 'user' is a FREE variable (used but not bound here)
    raise ValueError("...")
if not user.get("name"):
    raise ValueError("...")

# Block 1 - in process_admin_data()
if not admin.get("id"):       # 'admin' is a FREE variable
    raise ValueError("...")
if not admin.get("name"):
    raise ValueError("...")
```

These blocks unify successfully because they have identical structure. The unifier creates:
- Parameter `__param_0` for the dict being validated
- The free variable `user` becomes a parameter

But the generated calls are incorrect:
```python
# Block 0: ✓ Correct
__extracted_func(user, user)

# Block 1: ✗ Wrong - should use 'admin', not 'user'
__extracted_func(admin, user)
```

## Root Cause

The existing `_setup_bound_variable_alpha_renamings()` only tracks variables that are **BOUND** (assigned) within the unified blocks. It uses assignment position to match variables across blocks.

Free variables that differ across blocks are NOT tracked:
- `user` and `admin` are free variables (not assigned in the unified blocks)
- They get unified when comparing Name nodes
- But the correspondence is lost - hygienic_renames stays empty
- So `generate_call()` doesn't know to use `admin` in block 1

## Current Flow

1. **Unification**: Name nodes with `user` and `admin` unify successfully
2. **Parameterization**: Both become `__param_0` since they differ
3. **hygienic_renames**: Empty `{}` - no correspondence tracked!
4. **generate_call()**: Uses canonical name `user` for both blocks

## What's Needed

Track free variable correspondence during unification. When unifying Name nodes with different names across blocks:

1. Detect that names differ (`user` vs `admin`)
2. Record correspondence in `alpha_renamings`:
   - `(0, 'user') → 'user'` (canonical)
   - `(1, 'admin') → 'user'` (maps to canonical)
3. Export to `hygienic_renames` at end of unification
4. Use in `generate_call()` to look up original names

## Implementation Strategy

The fix should be in the Name node unification logic:

### Current Code (unifier.py ~line 800)
```python
def _unify_name_nodes(self, nodes, subst, block_indices):
    names = [n.id for n in nodes]
    if len(set(names)) == 1:
        return True  # All same name

    # Different names - try to parameterize
    return self._try_parameterize(nodes, subst, block_indices)
```

### Needed Enhancement
```python
def _unify_name_nodes(self, nodes, subst, block_indices):
    names = [n.id for n in nodes]

    if len(set(names)) == 1:
        return True  # All same name

    # Different names - record correspondence before parameterizing
    # Use first name as canonical
    canonical_name = names[0]
    for block_idx, node in zip(block_indices, nodes):
        if node.id != canonical_name:
            # Record that this block's name maps to canonical
            self.alpha_renamings[(block_idx, node.id)] = canonical_name

    # Now parameterize
    return self._try_parameterize(nodes, subst, block_indices)
```

## Test Cases Needed

1. **Free variables with different names** (example1_simple.py case)
   - Block 0: `user.get("id")`
   - Block 1: `admin.get("id")`
   - Should generate: `func(user)` and `func(admin)`

2. **Mix of free and bound variables**
   - Block 0: `result = user.id; return result`
   - Block 1: `output = admin.id; return output`
   - Should track both correspondences

3. **Multiple free variables**
   - Block 0: `user.id + config.value`
   - Block 1: `admin.id + settings.value`
   - Should track: user↔admin, config↔settings

4. **Free variable used multiple times**
   - Block 0: `user.id + user.name`
   - Block 1: `admin.id + admin.name`
   - Should consistently use original names

## Files to Modify

1. **src/towel/unification/unifier.py**
   - `_unify_nodes()` - Name node handling
   - Add correspondence tracking when names differ

2. **tests/test_free_variable_correspondence.py**
   - New test file with TDD tests

3. **src/towel/unification/extractor.py**
   - Already modified to use hygienic_renames (done in previous session)
   - Should work once hygienic_renames is populated correctly
