# Towel - Usage Guide

> **Supplementary API guide.** A how-to for the `UnificationRefactorEngine` API. The maintained overview and CLI workflow are in the [README](../README.md); current limitations are in [docs/KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Getting started with the API

There are three ways to use Towel, the code refactoring tool based on anti-unification:

### 1. Analyze a Single File

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)

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

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)

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

engine = UnificationRefactorEngine(max_parameters=5, min_lines=3)

# Analyze all Python files in a directory (recursive)
proposals = engine.analyze_directory("src/", recursive=True)

# Apply all proposals
for proposal in proposals:
    modified_files = engine.apply_refactoring_multi_file(proposal)

    for file_path, content in modified_files.items():
        with open(file_path, 'w') as f:
            f.write(content)
```

## Progress Modes & Termination Reason

When using the fixed-point directory refactoring loop (`refactor_directory_to_fixed_point`) or the `dry` CLI, you can control progress output:

| Mode    | Behavior |
|---------|----------|
| `tqdm`  | Rich progress bar showing applied count & queue length. |
| `auto`  | Attempts `tqdm`, falls back to a textual single-line bar. |
| `none`  | Suppresses all progress output (quiet for CI). |
| `detail`| Lists the discovered proposals (first 25) and the localized follow-ups after each application, on the `towel` logger at INFO. |

The default is `tqdm`; the CLI's `--progress` option accepts the same values.

Call signature returns `(results_dict, termination_reason)` where `termination_reason` is:

* `fixed_point` – No further proposals remain.
* `iteration_cap` – Stopped after `max_iterations` applied refactorings (the CLI's `--max-refactorings`). The default, 0, runs to a fixed point.

### Example (detail mode)

```python
engine = UnificationRefactorEngine()
results, reason = engine.refactor_directory_to_fixed_point(
    "my_project", "my_project_out", max_iterations=0, progress="detail"
)
print("Termination:", reason)
```

### Localized Follow-Ups

After each applied proposal, the engine re-analyzes only the changed files to enqueue *localized* follow-up proposals immediately. This accelerates chained extractions without rescanning the entire project every iteration. When that queue drains, a *global* pass re-pairs the project for cross-file duplicates; after the first, a global pass re-pairs only the functions in files rewritten since the previous global pass, which is exact (the argument is in [ARCHITECTURE.md](ARCHITECTURE.md#incremental-global-passes-and-why-they-are-exact)). Construct the engine with `incremental_global_passes=False` to re-pair everything each time; the output is byte-identical.

## Command-Line Usage

Preview opportunities read-only, then refactor into a fresh directory to diff
and adopt:

```bash
towel preview src/
towel dry src/ src_cleaned/ --no-interactive
```

`preview` lists each opportunity with the extracted helper and, per call site,
the original block next to the generated call; `dry` writes the refactored copy.
See the [README](../README.md) and [Quick start](QUICKSTART.md) for the full CLI.

## Configuration

### Engine Parameters

```python
engine = UnificationRefactorEngine(
    max_parameters=5,  # Maximum parameters for extracted functions
                       # (prevents over-parameterization)

    min_lines=3        # Minimum lines for a code block
                       # (smaller blocks are ignored)
)
```

Both are `--max-parameters` and `--min-lines` on the command line.

The remaining parameters (keyword-only after `parameterize_constants`), all defaulting to what the CLI does:

| Parameter | Default | Effect |
|---|---|---|
| `parameterize_constants` | `True` | Differing constants become helper parameters. |
| `prefer_absolute_imports` | `None` | Cross-file helper import style; `None` lets the discovered layout decide (`--prefer-absolute-imports/--no-prefer-absolute-imports`). |
| `pep420_namespace_packages` | `None` | Treat directories without `__init__.py` as packages; `None` infers it (`--pep420/--no-pep420`). |
| `excluded_directories` | `()` | Directory names skipped in directory mode (`--exclude`). |
| `max_candidate_pairs` | `20_000_000` | Most candidate block pairs one analysis evaluates; past it the largest groups of similar blocks are left out with a warning (`--max-pairs`). |
| `skip_trivial_helpers` | `True` | Do not propose a helper that only forwards, renames, or unpacks. |
| `reuse_existing_functions` | `True` | A duplicate that is the whole body of a plain module-level function calls that function instead of a new helper. |
| `annotate_helpers` | `True` | Copy the annotations the call sites declare onto the helper, in code that uses annotations. |
| `type_oracle` | `None` | A `TypeOracle` (`towel.type_inference`) that reveals types, decides subtyping, and checks generated code; without one nothing is inferred or verified (`--types/--no-types`). |
| `snippet_formatter` | `None` | Formats each inserted snippet; see below (`--format/--no-format`). |
| `file_finisher` | `None` | Finishes each modified file, for example by sorting its imports. |
| `incremental_global_passes` | `True` | Later global passes re-pair only rewritten files (exact). |
| `promote_equal_hof_literals` | `False` | Expose literal arguments of higher-order factory calls as helper parameters even when they are equal in every block. |
| `settings` | `None` | A `towel.diagnostics.Settings`: what Towel reads from the environment (worker cap, debug switches). When omitted, the engine reads the environment once at construction; the command line and the analysis session each read it once as well (see *Diagnostics and settings* in [ARCHITECTURE.md](ARCHITECTURE.md)). |

The CLI's `dry` command wires the formatter, import sorter, and type oracle
from the project's own configuration. Library callers can do the same:

```python
from pathlib import Path
from towel.formatting import formatter_for_project, import_sorter_for_project
from towel.type_inference import type_oracle_for_project

root = Path("src/")
formatter = formatter_for_project(root)   # ruff when configured, else Black
sorter = import_sorter_for_project(root)  # ruff's I rules or isort, when the project uses them
oracle = type_oracle_for_project(root)    # mypy, pyright, or both

engine = UnificationRefactorEngine(
    snippet_formatter=formatter.tool, file_finisher=sorter.tool, type_oracle=oracle.tool
)
```

Each result is a `ToolChoice`: its `tool` is None when nothing suitable is
installed, and its `note` says what was chosen and names a configured tool
that is not installed. Without a formatter the rendering is `ast.unparse`'s: one
statement per line, single-quoted strings, no blank-line conventions.

The engine checks the original project before using its type oracle. If that
check completes with type errors, it aborts: fix the errors or explicitly
rerun the CLI with `--no-types`. This option preserves existing source
annotations but generates unannotated helpers; library callers obtain the
same behavior with `type_oracle=None` and `annotate_helpers=False`. A checker
crash or timeout remains a distinct verification failure. A clean baseline
keeps prospective-project verification enabled throughout the run.

For new module-level helpers and helper methods, Towel also anti-unifies the
corresponding argument and result types. For example, `list[int] -> int` and `list[str] -> str` can become
`list[T] -> T`. Already-generic callers receive fresh helper binders with their
supported bounds or constraints preserved. The generated `TypeVar` declarations
work on Python 3.11 and newer. Instance and class helpers retain parameters
bound by the host class; static helpers infer fresh parameters from explicit
arguments. Fresh method parameters are declared before the host class
at module scope. Every candidate is checked with its rewritten calls
and unchanged consumers before it is accepted; `--no-types` does not synthesize
generic contracts. See the [design and supported boundaries](proposals/type-parameters.md).

Each fixed-point call starts a new run and checks the original before creating
an output copy. Direct `apply_refactoring` calls share an implicit run; call
`engine.begin_refactoring_run(paths)` when starting a separate run on the same
engine. The caller owns the oracle and should call `oracle.tool.close()` in a
`finally` block when `oracle.tool` is not `None`.

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
- `venv`, `env`, `node_modules`, and any directory holding a `pyvenv.cfg`
- The names in `excluded_directories` (`--exclude`)
- Symlinked files, and Towel's own `_towel_probe_*.py` type-checker probes

## Understanding Proposals

Each proposal contains:

```python
proposal.description          # "Extract common code from func1 and func2"
proposal.parameters_count     # Number of parameters in extracted function
proposal.extracted_function   # The AST of the new function
proposal.replacements         # List of Replacement dataclasses: line_range, node (the
                              # generated call statement), file_path, class_name,
                              # method_kind, implicit_param
proposal.file_path            # Canonical location for the extracted function
proposal.reused_function      # ReusedFunction(name, file_path, line_range) when the
                              # sites call an existing function; None for a helper
proposal.required_imports     # Imports the host needs for the helper's annotations
proposal.helper_type_declarations  # Fresh generic declarations, materialized with the helper
proposal.return_variables     # Names the helper returns, in the call's unpacking order
proposal.insert_into_class    # The class the helper becomes a method of, if any;
proposal.method_kind          # instance, class, or static, with insert_into_function
                              # for a helper nested in a common enclosing function
proposal.source_digests       # The file digests the proposal was computed from; applying
                              # a stale proposal raises ChangeConflict("Stale proposal")
```

### Cross-File vs Same-File

```python
# Check if it's cross-file
is_cross_file = any(r.file_path not in (None, proposal.file_path) for r in proposal.replacements)

if is_cross_file:
    # Multiple files affected - use multi-file method
    modified_files = engine.apply_refactoring_multi_file(proposal)
else:
    # Single file - simpler method works
    refactored = engine.apply_refactoring(proposal.file_path, proposal)
```

## Examples

The outputs below are what `towel dry` writes with the defaults and Black
installed. Through the API without a `snippet_formatter`, strings come out
single-quoted and the layout is `ast.unparse`'s.

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
def __extracted_func_0(__param_0):
    if not __param_0.get("id"):
        raise ValueError("User ID is required")
    if not __param_0.get("name"):
        raise ValueError("User name is required")
    if len(__param_0.get("name", "")) < 2:
        raise ValueError("User name too short")
    return __param_0


def process_user_data(user_id):
    user = {"id": user_id, "name": "John"}
    return __extracted_func_0(user)

def process_admin_data(admin_id):
    admin = {"id": admin_id, "name": "Jane", "role": "admin"}
    return __extracted_func_0(admin)
```

The trailing `return` joined the block because it is the same statement in
both functions up to the renamed variable, so the call site becomes a
`return` of the helper.

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

**After (file1.py):** unchanged. The duplicate is the whole body of
`calculate_discount_for_regular_customer`, so no helper is generated: the
first-defined function is kept and the other calls it (the preview reports
this as "Reuse calculate_discount_for_regular_customer (file1.py)").

**After (file2.py):**
```python
from .file1 import calculate_discount_for_regular_customer
def calculate_discount_for_premium_customer(price, customer):
    return calculate_discount_for_regular_customer(price, customer)
```

The import is relative because both modules sit in one package. Had the
shared block been only part of each body, a `__extracted_func_0` helper would
have been placed in one file and imported by the other in the same way.

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
- Differing constants become parameters by default; with `parameterize_constants=False` they must match

### "Too many proposals"

- Increase `min_lines` to focus on larger duplicates
- Decrease `max_parameters` to avoid over-parameterized functions
- Raise `DEFAULT_SIMILARITY_THRESHOLD` in `block_signature.py` (0.6; it is not an
  engine option) so that fewer loosely similar pairs reach unification

### Import errors after refactoring

Towel writes a relative import for a cross-file helper inside a package
(`from .module import helper`), so the result imports correctly whether you
refactor in place or adopt an out-of-place copy into its real location. If you
still hit an import error:

- Refactor through `analyze_files()`/`analyze_directory()` or the `towel dry`
  command, which run the full pipeline including import-path inference; a
  hand-built `RefactoringProposal` skips it.
- For an unusual layout, declare the build backend and source roots in
  `pyproject.toml` so the module names resolve; an unresolvable layout is
  reported rather than guessed.

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
    lines_saved = sum(r.line_range[1] - r.line_range[0] for r in proposal.replacements)
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
