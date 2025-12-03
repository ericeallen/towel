# Overlap Filtering Fix

## Problem

The DRY detector was generating **multiple overlapping proposals** for the same code and applying **ALL of them sequentially**, which caused catastrophic failures:

### Symptoms
- Running `just analyze test_examples` would find 12 proposals
- All 12 proposals were for the same pair of functions (overlapping sub-blocks)
- The tool applied all 12 sequentially, causing:
  - Invalid line number references (as each refactoring changed line numbers)
  - Completely broken Python files with:
    - Functions without proper definitions
    - Code fragments at module level
    - Recursive calls to non-existent functions
    - Invalid syntax throughout

### Root Cause

The `_extract_code_blocks()` method in the refactor engine extracts **every possible contiguous sub-sequence** from function bodies:

```python
for length in range(len(body), 0, -1):
    for start in range(len(body) - length + 1):
        block = body[start:start + length]
        # ... adds this block as a proposal
```

For a 7-statement function, this creates proposals for:
- Lines 1-7 (full function)
- Lines 1-6, 2-7 (6-line sub-blocks)
- Lines 1-5, 2-6, 3-7 (5-line sub-blocks)
- ... and so on

Many of these overlap (e.g., lines 1-7 overlaps with lines 1-6, 2-7, 1-5, etc.)

The `analyze_directory.py` script then blindly applied **ALL** proposals:

```python
for i, proposal in enumerate(proposals, 1):
    # Applied every single proposal without checking for overlaps!
    engine.apply_refactoring_multi_file(proposal)
```

Each application modified the file, invalidating line numbers for subsequent proposals.

## Solution

Added **overlap detection and filtering** to keep only the best non-overlapping proposals:

### Implementation

Created `filter_overlapping_proposals()` function that:

1. **Detects affected lines**: For each proposal, identifies all `(file_path, line_number)` tuples it would modify
2. **Sorts by size**: Larger extractions are preferred (assumed to be better)
3. **Greedy selection**: Selects proposals in order, skipping any that overlap with already-selected ones

```python
def filter_overlapping_proposals(proposals: List[RefactoringProposal]) -> List[RefactoringProposal]:
    """
    Filter proposals to remove overlaps, keeping the best ones.

    Strategy: Sort by code size (larger first), then greedily select
    non-overlapping proposals.
    """
    # Sort by total lines affected (larger extractions first)
    sorted_proposals = sorted(proposals, key=proposal_size, reverse=True)

    # Greedy selection: take largest non-overlapping proposals
    selected = []
    used_lines = set()

    for proposal in sorted_proposals:
        affected = get_affected_lines(proposal)

        # Check if this proposal overlaps with already selected ones
        if not (affected & used_lines):
            selected.append(proposal)
            used_lines.update(affected)

    return selected
```

### Files Updated

1. **`analyze_directory.py`**: Main analysis script - added filtering before applying proposals
2. **`simple_example.py`**: Preview script - added filtering for consistency
3. **`apply_cross_file_refactor.py`**: Cross-file refactoring script - added filtering

## Results

### Before Fix
```
Found 12 refactoring opportunities

Applying 1/12: ...
Applying 2/12: ...
...
Applying 12/12: ...

# Result: Completely broken Python files
```

### After Fix
```
Found 12 refactoring opportunities
Filtered to 1 non-overlapping proposals
(Removed 11 overlapping proposals)

Applying 1/1: Extract common code from calculate_discount_for_regular_customer...

✓ Applied 1 refactorings

# Result: Clean, working Python files
```

### Verification

The refactored code:
- **example3_file1.py**: Defines `extracted_func()` with common logic
- **example3_file2.py**: Imports and calls `extracted_func()`
- Both files are syntactically valid
- Code runs correctly and produces expected output

## Testing

Run the comprehensive test:

```bash
just reset-examples
python3 test_overlap_fix.py
```

Or test the complete workflow:

```bash
# 1. Preview (read-only)
just example

# 2. Apply refactoring
just analyze test_examples

# 3. Verify it works
cd test_examples
python3 -c "from example3_file1 import process_regular_order; ..."
```

## Future Improvements

Potential enhancements:
1. **Better ranking**: Instead of just size, consider code complexity or duplication count
2. **Engine-level filtering**: Move filtering into `UnificationRefactorEngine.analyze_files()` to avoid duplicating the logic
3. **Interactive selection**: Allow users to choose which proposals to apply
4. **Conflict detection**: Warn if multiple large non-overlapping opportunities exist in the same function
