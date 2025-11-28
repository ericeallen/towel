# Towel Refactoring Engine Architecture

## Overview

Towel is an automated code refactoring engine that uses **unification-based duplicate detection** to identify and extract common code patterns across Python codebases. The system employs compiler-style analysis phases and AST (Abstract Syntax Tree) manipulation to safely extract duplicated code into reusable functions.

## Core Design Principles

1. **Correctness First**: All refactorings must preserve program semantics
2. **Compiler-Style Pipeline**: Sequential analysis phases with clear inputs/outputs
3. **Nominal Unification**: Variables unify by name (α-equivalence), not structure
4. **Scope Safety**: Never create orphaned variables or break scope relationships
5. **Cross-File Support**: Detect and refactor duplications spanning multiple files

---

## System Architecture

### High-Level Data Flow

```
Input Files
    ↓
[Parse & Normalize]  ← AST parsing, assignment normalization
    ↓
[Scope Analysis]     ← Variable binding detection
    ↓
[Class Collection]   ← Class hierarchy extraction
    ↓
[Function Collection] ← Function enumeration
    ↓
[Block Pairing]      ← Candidate duplication identification
    ↓
[Unification]        ← Attempt to unify block pairs
    ↓
[Overlap Filtering]  ← Remove conflicting proposals
    ↓
Refactoring Proposals
```

---

## Module Organization

### Core Modules (by Layer)

#### **Layer 1: Data Models**
- **models.py** - Core data structures
  - `ParsedModule`: Represents a parsed Python file with AST and metadata
  - `FunctionArtifact`: Function with scope and context information
  - `ClassInfo`: Class hierarchy information
  - `CodeBlockPair`: Pair of potentially duplicate code blocks
  - `RefactoringProposal`: A proposed refactoring with replacements
  - `Replacement`: Single replacement site in refactoring

#### **Layer 2: AST Analysis**
- **ast_normalizer.py** - AST canonicalization
  - Converts `x = x + 1` → `x += 1` for consistency
  - Canonical arithmetic expression ordering
  - Ensures structural equivalence before unification

- **scope_analyzer.py** - Variable scope analysis
  - Builds scope trees for modules/classes/functions
  - Tracks variable bindings and references
  - Critical for orphan detection

- **binding_detector.py** - Nominal binding detection
  - Identifies which names are bound by statements
  - Supports all Python binding constructs (assignments, loops, comprehensions, etc.)
  - Used by nominal unification algorithm

- **assignment_analyzer.py** - Assignment chain analysis
  - Detects which variables are "written before read"
  - Determines return variables for extracted functions
  - Handles complex assignment patterns

#### **Layer 3: Unification**
- **unifier.py** - Structural AST unification
  - Unifies two AST structures by finding variable substitutions
  - Returns substitution mapping and combined free variables
  - Handles complex expressions, statements, and control flow

- **nominal_unifier.py** - Nominal unification wrapper
  - Wraps structural unifier with binding-aware logic
  - Variables must match by name in binding positions
  - Prevents invalid α-renaming

#### **Layer 4: Duplication Detection**
- **block_signature.py** - Fast similarity pre-filtering
  - Computes structural fingerprints of code blocks
  - Statement count, control flow presence, identifier counts
  - Rejects obviously different blocks before expensive unification

- **orphan_detector.py** - Scope safety validation
  - Ensures extraction won't orphan variables
  - Checks that all used variables are either:
    - Parameters to extracted function
    - Defined within extracted block
    - Available in all call sites

#### **Layer 5: Code Extraction**
- **extractor.py** - Function extraction logic
  - Generates new function definitions from unified blocks
  - Creates parameter lists from free variables
  - Handles return value generation
  - Supports method extraction (instance/class/static)

#### **Layer 6: Orchestration**
- **pipeline.py** - Compiler-style phase orchestration
  - Sequential pipeline execution with caching
  - Progress bar support for long-running analyses
  - Cache invalidation for incremental analysis

- **refactor_engine.py** - Main refactoring engine
  - High-level API (`analyze_files`, `analyze_directory`)
  - Block pairing logic across functions/files
  - Overlap detection and filtering
  - Iterative refactoring application

#### **Layer 7: Utilities**
- **visitors.py** - AST visitor utilities
  - `FunctionCollector`: Enumerates all function definitions
  - Custom visitors for various analyses

- **project_layout.py** - Import path generation
  - Generates relative import paths for cross-file refactoring
  - Handles package structure and module resolution

- **exceptions.py** - Domain-specific exceptions
  - `TowelError`: Base exception
  - `UnificationError`: Unification failures
  - `OrphanVariableError`: Scope safety violations
  - `ParseError`, `RefactoringError`, etc.

- **builtins.py** - Python builtin tracking
  - List of Python built-in names to avoid shadowing

---

## Pipeline Phases (Detailed)

### Phase 1: Parse Modules
**Input**: List of file paths
**Output**: `List[ParsedModule]`

1. Read source code from disk
2. Parse with `ast.parse()`
3. Apply AST normalizations:
   - `normalize_assigns_to_augassigns()` - `x = x + 1` → `x += 1`
   - `canonicalize_arithmetic()` - Order-independent comparison
4. Create `ParsedModule` objects

**Error Handling**: Invalid files are skipped gracefully

### Phase 2: Analyze Scopes
**Input**: `List[ParsedModule]`
**Output**: Modules with attached `scope_analyzer` and `root_scope`

1. Create `ScopeAnalyzer` instance per module
2. Walk AST to build scope tree
3. Track variable bindings and usages
4. Attach analyzer to module for downstream phases

**Key Insight**: Scope information is required for all subsequent phases

### Phase 3: Collect Classes
**Input**: `List[ParsedModule]`
**Output**: `List[ClassInfo]`

1. Visit all `ClassDef` nodes
2. Extract class name, qualified name, and base classes
3. Handle nested classes (e.g., `Outer.Inner`)
4. Resolve base class names (supports `Base` and `module.Base`)

**Use Case**: Method extraction into base classes requires hierarchy knowledge

### Phase 4: Collect Functions
**Input**: `List[ParsedModule]`
**Output**: `List[FunctionArtifact]`

1. Visit all `FunctionDef` and `AsyncFunctionDef` nodes
2. Record enclosing class and function context
3. Attach scope analyzer and root scope
4. Build ancestry chain for nested functions

**Error Handling**: Raises if scope analysis wasn't run (strict dependency)

### Phase 5: Pair Blocks
**Input**: Engine, `List[FunctionArtifact]`
**Output**: `List[CodeBlockPair]`

1. Enumerate all function pairs (O(n²) in function count)
2. For each function pair, extract candidate blocks:
   - Minimum line count (configurable, default 4)
   - Maximum line count (configurable, default 15)
3. Compute block signatures for fast filtering
4. Pair blocks if signatures are compatible
5. Optional progress bar for large projects

**Optimization**: Signature-based filtering rejects >95% of pairs early

### Phase 6: Unify Blocks
**Input**: Engine, `List[CodeBlockPair]`, functions, classes
**Output**: `List[RefactoringProposal]`

For each block pair:
1. **Signature Check**: Verify structural similarity
2. **Unification**: Attempt to unify AST structures
   - Find variable substitution mapping
   - Check α-equivalence (nominal unification)
3. **Orphan Detection**: Ensure no variables would be orphaned
4. **Assignment Analysis**: Determine return variables
5. **Extraction**: Generate extracted function definition
6. **Replacement Generation**: Create call sites for extracted function
7. **Create Proposal**: Package into `RefactoringProposal`

**Progress**: Optional progress bar showing unification attempts

### Phase 7: Filter Overlaps
**Input**: `List[RefactoringProposal]`
**Output**: `List[RefactoringProposal]` (de-duplicated)

1. Detect overlapping line ranges across proposals
2. Remove lower-benefit proposals that conflict
3. Ensure no two proposals modify same code

**Heuristic**: Prefer proposals with more parameters (more general)

---

## Key Algorithms

### Nominal Unification Algorithm

**Goal**: Find variable substitution `σ` such that `σ(block1) ≡ σ(block2)`

**Constraint**: Variables that bind names must unify nominally (by name)

```
unify_nominal(block1, block2):
    1. Compute bindings: B1 = bindings(block1), B2 = bindings(block2)
    2. If B1 ≠ B2: FAIL (different binding structure)
    3. Call structural unifier: σ = unify_structural(block1, block2)
    4. For each (var1, var2) in σ:
        - If var1 ∈ B1 and var2 ∈ B2:
            - Require var1 == var2 (nominal match)
            - Else: FAIL (invalid α-renaming)
    5. Return σ
```

**Example**:
```python
# Block 1
for x in items:
    print(x)

# Block 2
for x in values:  # ✓ binds same name 'x'
    print(x)

# Block 3
for y in values:  # ✗ binds different name 'y'
    print(y)       # nominal unification FAILS
```

### Orphan Detection Algorithm

**Goal**: Ensure extracted function is callable from all use sites

```
check_orphans(block, free_vars, call_site_scopes):
    used_vars = compute_used_variables(block)

    for var in used_vars:
        if var in free_vars:
            # Will be a parameter - check availability
            for scope in call_site_scopes:
                if not scope.has_binding(var):
                    RAISE OrphanVariableError(var)

        # else: var is defined in block, always safe
```

**Example**:
```python
def func1():
    x = 10
    y = x + 5  # Can extract this line

def func2():
    y = ??? + 5  # Cannot extract - 'x' not available here
```

### Assignment Analysis Algorithm

**Goal**: Determine which variables must be returned from extracted function

```
analyze_assignments(block, free_vars):
    1. Compute written_vars = all variables assigned in block
    2. Compute read_after = variables read after block in original context
    3. Return written_vars ∩ read_after ∩ (¬free_vars)
```

**Example**:
```python
x = compute()  # ← extract this
y = x * 2
total = y + 10
return total   # 'y' is read after block

# Extracted function must return 'y':
def extracted():
    x = compute()
    y = x * 2
    return y  # ← must return
```

---

## Cross-File Refactoring

### Import Generation

When extracting to a different file from call sites:

1. Determine target file for extracted function
2. Compute relative import path from call site files
3. Generate import statement: `from <module> import <function>`
4. Insert import at top of calling file

**Module Path Resolution** ([project_layout.py:98](src/towel/unification/project_layout.py#L98)):
```python
def compute_import_path(source_file, target_file, package_root):
    # Convert file paths to module paths
    # Handle package hierarchies
    # Return "from X import Y" statement
```

### Multi-File Coordination

- All files analyzed together in single pipeline run
- Proposals can span multiple files
- Single extracted function serves all call sites
- Overlap detection works across files

---

## Configuration & Tuning

### Engine Parameters

```python
UnificationRefactorEngine(
    min_lines=4,           # Minimum block size to consider
    max_lines=15,          # Maximum block size to extract
    max_parameters=5,      # Reject if too many parameters needed
    enable_extraction=True # Enable actual extraction (vs. detection only)
)
```

### Progress Modes

- `progress="none"`: No progress output
- `progress="auto"`: Use tqdm if available, else inline
- `progress="tqdm"`: Force tqdm (falls back if unavailable)

---

## Error Handling & Safety

### Exceptions

- **UnificationError**: Blocks cannot be unified
- **OrphanVariableError**: Extraction would orphan variables
- **ScopeAnalysisError**: Cannot determine bindings
- **RefactoringError**: Cannot apply proposal
- **ParseError**: Invalid Python syntax

### Safety Guarantees

1. **No Orphaned Variables**: Orphan detector prevents extraction that breaks scope
2. **Preserved Semantics**: Nominal unification ensures variable roles are consistent
3. **Type Safety**: All AST manipulations preserve Python syntax
4. **Graceful Degradation**: Invalid files are skipped, not fatal

---

## Performance Characteristics

### Time Complexity

- **Parse**: O(n) where n = total lines of code
- **Scope Analysis**: O(n)
- **Function Collection**: O(m) where m = number of functions
- **Block Pairing**: O(m² × k) where k = average blocks per function
- **Unification**: O(p × b) where p = pairs, b = block size
- **Overlap Filter**: O(proposals²)

**Typical Bottleneck**: Block pairing (quadratic in function count)

### Space Complexity

- **AST Storage**: O(n) - one AST per source file
- **Scope Trees**: O(n)
- **Block Pairs**: O(m² × k) - can be large for big projects
- **Proposals**: O(successful pairs)

### Optimizations

1. **Signature-Based Filtering**: Rejects 95%+ of pairs in <1ms
2. **Caching**: Parse/scope/class/function results cached per file
3. **Incremental Analysis**: `invalidate_paths` for changed files only
4. **Progress Bars**: Async progress display doesn't block analysis

---

## Extension Points

### Custom Visitors

Add new AST visitors in [visitors.py](src/towel/unification/visitors.py):

```python
class MyVisitor(ast.NodeVisitor):
    def visit_ClassDef(self, node):
        # Custom logic
        self.generic_visit(node)
```

### Custom Normalizations

Extend [ast_normalizer.py](src/towel/unification/ast_normalizer.py):

```python
def my_normalization(tree: ast.AST) -> ast.AST:
    # Transform AST
    return tree
```

Add to pipeline phase 1.

### Custom Filters

Add signature checks in [block_signature.py](src/towel/unification/block_signature.py):

```python
def my_filter(sig1: BlockSignature, sig2: BlockSignature) -> bool:
    # Return True if blocks should be compared
    return ...
```

---

## Testing Strategy

### Test Pyramid

1. **Unit Tests**: Individual module functions
   - `test_scope_analyzer.py`
   - `test_binding_detector.py`
   - `test_nominal_unifier.py`
   - etc.

2. **Integration Tests**: Multi-module workflows
   - `test_pipeline_phases.py`: Phase composition
   - `test_crossfile_integration.py`: Cross-file refactoring

3. **Regression Tests**: Baseline output comparison
   - `test_regression.py`: Compares against golden outputs

4. **Observational Equivalence Tests**: Runtime behavior preservation
   - `test_observational_equivalence.py`: Executes before/after code

### Coverage Targets

- **Core Modules**: 100% (unifier, scope_analyzer, binding_detector, etc.)
- **Pipeline**: 75%+ (tested via integration tests)
- **Overall**: 85%+

---

## Future Architecture Improvements

### Potential Enhancements

1. **Incremental Refactoring**: Apply proposals one-by-one with re-analysis
2. **Type-Aware Unification**: Use type annotations to improve matching
3. **Semantic Equivalence**: Beyond syntactic unification (e.g., `x + 1` ≡ `1 + x`)
4. **IDE Integration**: LSP server for real-time refactoring suggestions
5. **Parallelization**: Multi-process block pairing for large projects
6. **Machine Learning**: Learn which refactorings are most useful

### Known Limitations

1. **Dynamic Code**: Cannot analyze `exec()`, `eval()`, or dynamic imports
2. **Metaprogramming**: Decorator logic not fully understood
3. **Side Effects**: Cannot detect all side effect interactions
4. **Heuristic Overlap Filtering**: May miss some valid combinations

---

## References

### Key Papers & Concepts

- **α-equivalence**: Lambda calculus equivalence modulo variable renaming
- **Unification**: Term unification in first-order logic
- **Scope Graphs**: Formal scope modeling (not currently used, but related)
- **Clone Detection**: Broader field of duplicate code detection

### Related Tools

- **rope**: Python refactoring library (structural, not unification-based)
- **jedi**: Python auto-completion (uses similar scope analysis)
- **libcst**: Concrete syntax tree for Python (preserves formatting)

---

## Glossary

- **AST**: Abstract Syntax Tree - compiler IR for source code
- **α-equivalence**: Equivalence modulo variable renaming
- **Nominal Unification**: Unification where variable names must match
- **Orphaned Variable**: Variable whose definition would be separated from usage
- **Free Variable**: Variable used but not defined in a block
- **Bound Variable**: Variable defined (assigned) in a block
- **Substitution**: Mapping from variables to expressions (σ: Var → Expr)
- **Scope**: Region of code where a variable binding is valid
