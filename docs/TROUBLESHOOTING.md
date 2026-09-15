# Troubleshooting Guide

> **Supplementary API guide.** A how-to for the `UnificationRefactorEngine` API. The maintained overview and CLI workflow are in the [README](../README.md); current limitations are in [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Common Issues and Solutions

### IndexError: list index out of range

**Error:**
```
IndexError: list index out of range
  File "towel/unification/refactor_engine.py", line 649
    indent = self._get_indent(lines[start_line - 1])
```

**Cause:** The files being analyzed have been corrupted by previous refactoring runs, making them shorter than expected. The line numbers in the proposals refer to lines that no longer exist.

**Solution:**
```bash
# Reset the example directories
just reset-all-examples

# Or reset specific directories
just reset-examples         # Resets test_examples/
just reset-examples-copy    # Resets test_examples_copy/
```

**Prevention:** Always work on fresh copies of files or use version control (git) to restore files between runs.

---

### No refactoring opportunities found

**Issue:** Running `just analyze <dir>` finds 0 opportunities even though duplicates exist.

**Possible Causes:**

1. **min_lines too high** - Code blocks are smaller than the threshold
   ```python
   # Try lowering it
   engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)
   ```

2. **max_parameters too low** - Duplicates differ in too many ways
   ```python
   # Try increasing it
   engine = UnificationRefactorEngine(max_parameters=10, min_lines=4)
   ```

3. **Constants differ** - The tool requires constants to match exactly
   ```python
   # This WON'T be unified (different constants):
   x = 5  # vs  x = 10
   name = "John"  # vs  name = "Jane"
   ```

4. **Structural differences** - Blocks are too structurally different
   - Different number of statements
   - Different AST node types
   - Different control flow

---

### Too many refactoring opportunities

**Issue:** Tool finds hundreds of opportunities, most of which are low-value.

**Solutions:**

1. **Increase min_lines** to focus on larger duplicates:
   ```bash
   # In code:
   engine = UnificationRefactorEngine(max_parameters=5, min_lines=6)
   ```

2. **Decrease max_parameters** to avoid over-parameterization:
   ```bash
   engine = UnificationRefactorEngine(max_parameters=2, min_lines=4)
   ```

3. **Filter proposals** by quality:
   ```python
   # Only keep proposals with 1-2 parameters
   good_proposals = [p for p in proposals if p.parameters_count <= 2]
   ```

---

### Cross-file duplicates not detected

**Issue:** Running the tool finds duplicates within files but not across files.

**Cause:** Using `analyze_file()` instead of `analyze_files()` or `analyze_directory()`.

**Solution:**
```python
# ❌ WRONG - Analyzes files separately
proposals1 = engine.analyze_file("file1.py")
proposals2 = engine.analyze_file("file2.py")

# ✅ CORRECT - Analyzes files together
proposals = engine.analyze_files(["file1.py", "file2.py"])

# ✅ CORRECT - Analyzes entire directory
proposals = engine.analyze_directory("src/")
```

Or use the just commands:
```bash
# ✅ CORRECT
just analyze src/
```

---

### Import errors after refactoring

**Issue:** After applying refactorings, imports are incorrect or missing.

**Solutions:**

1. **Ensure using multi-file analysis:**
   ```python
   # Use analyze_directory() or analyze_files()
   proposals = engine.analyze_directory("src/")
   ```

2. **Check module names:**
   - Imports are based on file names
   - `example3_file1.py` → `from example3_file1 import func`
   - Ensure files are in the same directory or have proper package structure

3. **Verify import placement:**
   - Check that imports are after docstrings
   - Check that imports are with other imports

---

### Extracted function has wrong parameters

**Issue:** Extracted function has too many parameters or wrong parameter names.

**Causes:**

1. **Free variables detected as parameters** - Variables from enclosing scope are being parameterized
   - This is correct behavior - the tool passes all free variables as parameters

2. **Generic parameter names** - Parameters named `param_0`, `param_1`, etc.
   - This is current limitation
   - See [Future Improvements](#future-improvements)

**Workaround:** Manually rename parameters after extraction.

---

### Syntax errors in refactored code

**Issue:** After refactoring, Python files have syntax errors.

**Solutions:**

1. **Check the output immediately:**
   ```python
   import ast
   try:
       ast.parse(refactored_code)
       print("✓ Valid Python")
   except SyntaxError as e:
       print(f"✗ Syntax error: {e}")
   ```

2. **Report the bug** with:
   - Original files
   - Refactored output
   - Error message

3. **Use version control** to revert:
   ```bash
   git checkout <file>
   ```

---

### Tool is too slow

**Issue:** Analyzing large codebases takes too long.

**Solutions:**

1. **Increase min_lines** to reduce comparisons:
   ```python
   engine = UnificationRefactorEngine(max_parameters=5, min_lines=6)
   ```

2. **Analyze smaller directories:**
   ```bash
   # Instead of entire codebase
   just analyze src/module/
   ```

3. **Use non-recursive mode:**
   ```python
   proposals = engine.analyze_directory("src/", recursive=False)
   ```

---

### Files in wrong state after refactoring

**Issue:** Files are corrupted or have infinite recursion after applying refactorings.

**Cause:** This was an issue with the old implementation. The new unification-based approach fixes this.

**Solution:**

1. **Ensure using the unification-based engine:**
   ```python
   from towel.unification.refactor_engine import UnificationRefactorEngine
   # Not: from towel.refactoring.engine import RefactoringEngine
   ```

2. **Reset files:**
   ```bash
   just reset-all-examples
   ```

3. **Use version control** to track changes.

---

## Getting Help

### Check the logs

The tool now shows warnings for invalid line ranges:
```
Warning: Invalid line range 50-60 for file.py (file has 40 lines)
Skipping this replacement
```

### Debug mode

Add print statements to see what's happening:
```python
for i, proposal in enumerate(proposals):
    print(f"{i}. {proposal.description}")
    print(f"   Params: {proposal.parameters_count}")
    print(f"   File: {proposal.file_path}")
    print()
```

### Minimal reproduction

Create a minimal example that reproduces the issue:
```python
# test.py with minimal duplicate code
def func1():
    x = 1
    y = 2
    return x + y

def func2():
    a = 1
    b = 2
    return a + b
```

Then test:
```bash
just analyze .
```

---

## Future Improvements

Known limitations being worked on:

1. **Generic parameter names** - Will generate meaningful names like `data`, `user`, etc.
2. **N-way matching** - Currently compares pairs, will support N instances at once
3. **Better filtering** - Smarter proposals based on code quality metrics
4. **Performance** - Faster analysis of large codebases

See [UNIFICATION_IMPLEMENTATION.md](UNIFICATION_IMPLEMENTATION.md) for details.
