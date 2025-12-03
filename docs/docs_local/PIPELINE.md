# Refactoring Pipeline Architecture

This document describes the compiler-style pipeline used by Towel to discover and safely extract duplicate code via unification. Each phase transforms inputs (AST + auxiliary analysis structures) into richer artifacts that subsequent phases consume. The design emphasizes:

- Deterministic, ordered phases (no hidden side effects)
- Explicit data structures passed forward (no implicit global state)
- Separation of concerns (parsing vs. scoping vs. pairing vs. unification)
- Testability (each phase can be unit-tested with synthetic inputs)

The top-level orchestration lives in `src/towel/unification/pipeline.py` via `run_pipeline()`.

```
+--------------+      +--------------+      +------------------+      +------------------+
| 1. Parse     | ---> | 2. Scopes    | ---> | 3. Class Table   | ---> | 4. Function Col. |
+--------------+      +--------------+      +------------------+      +------------------+
                                                                     |
                                                                     v
                                                              +------------------+      +------------------+      +------------------+
                                                              | 5. Block Pairing | ---> | 6. Unification   | ---> | 7. Overlap Filter |
                                                              +------------------+      +------------------+      +------------------+
```

## Core Data Structures

These dataclasses (defined in `models.py`) encapsulate data flowing through the pipeline:

### `ParsedModule`
Represents a parsed Python module.
- `file_path: str` – Absolute or relative path
- `source: str` – Raw source text
- `tree: ast.AST` – Parsed AST module node
- `scope_analyzer: ScopeAnalyzer | None` – Attached after scope phase
- `root_scope: Scope | None` – Root scope object
- `class_infos: List[ClassInfo]` – Filled during phase 3 (class collection)

### `ClassInfo`
Summarizes one discovered class definition.
- `name: str` – Simple name
- `qualname: str` – Dot-qualified including nesting (e.g. `Outer.Inner`)
- `file_path: str` – Defining file
- `bases: List[str]` – Resolved base class names (dotted where possible)

### `FunctionArtifact`
Represents a function (sync or async) discovered at any nesting level.
- `file_path: str`
- `node: ast.AST` – `FunctionDef` or `AsyncFunctionDef`
- `scope_analyzer: ScopeAnalyzer` – Shared analyzer for its module
- `root_scope: Scope` – Module root scope
- `class_name: Optional[str]` – Enclosing class name if method
- `enclosing_function: Optional[str]` – Immediate enclosing function (for nested local functions)
- `ancestry: List[str]` – List of outer function names (outermost → innermost)

### `CodeBlockPair`
Candidate duplicate block pairing across one or two functions.
- Contains source references, block ranges, AST node lists, scope analyzers, ancestry, class context.

### `RefactoringProposal`
A unification-derived extraction plan.
- `extracted_function: ast.FunctionDef` – The synthesized helper/method
- `replacements: List[Replacement]` – Call sites to insert
- `insert_into_class` / `insert_into_function` – Placement hints
- `method_kind`, `method_param_name` – Method dispatch metadata
- `return_variables` – Names returned for lifetime correctness

## Phase Details

### 1. Parse Modules (`parse_modules`)
Input: Sequence of file paths.
Output: `List[ParsedModule]` with raw ASTs.
Responsibilities:
- Read source from disk
- Parse AST with `ast.parse`
- Skip unreadable / syntactically invalid files (robustness)
Invariants:
- No semantic modifications are performed.
- Ordering of modules preserved from input.

### 2. Analyze Scopes (`analyze_scopes`)
Input: Parsed modules.
Output: Same modules augmented with `ScopeAnalyzer` + `root_scope`.
Responsibilities:
- Walk AST to build nested scope structures (functions, classes, comprehensions, etc.)
- Record bindings, free variables, global/nonlocal declarations.
Invariants:
- Must be run before function-level free variable inference or lifetime checks.

### 3. Collect Classes (`collect_classes`)
Input: Scope-augmented modules.
Output: `List[ClassInfo]` plus each module's `class_infos` field.
Responsibilities:
- Traverse AST collecting class definitions
- Resolve base class names into dotted textual form where possible
Edge Cases:
- Nested classes contribute `qualname` polymorphism
- Unresolvable bases omitted (safe fallback)

### 4. Collect Functions (`collect_functions`)
Input: Modules + class info.
Output: `List[FunctionArtifact]` (all functions, nested included).
Responsibilities:
- Record each function's enclosing class & enclosing function
- Preserve ancestry for deepest common enclosing (DCE) decisions
- Distinguish methods vs. free functions for later method promotion logic

### 5. Pair Blocks (`pair_blocks`)
Input: Function artifacts.
Output: `List[CodeBlockPair]` candidates.
Responsibilities:
- Enumerate contiguous statement blocks inside functions
- Apply quick structural & heuristic filters (length, similarity signature)
- Consider cross-file pairs
Invariants:
- No mutation of original ASTs
- Pairs carry necessary scope references for later free variable analysis

### 6. Unify Blocks (`unify_blocks`)
Input: Block pairs, class table, function artifacts.
Output: Raw `List[RefactoringProposal]` (may overlap).
Responsibilities:
- Attempt AST unification with parameterization constraints
- Enforce max parameter count, f-string preservation, augmented assignment handling
- Perform global/nonlocal safety adjustments (declarations promoted, parameters elided)
- Generate hygienic helper/method definitions
- Build replacement call nodes, adjusting for method dispatch semantics
Validation Steps:
- Orphan variable detection (no undefined use-after extraction)
- Lifetime checks (variables cannot be bound after block but used inside)
- Return propagation & value-producing block determination

### 7. Overlap Filter (`filter_overlaps`)
Input: Possibly overlapping proposals.
Output: Greedy non-overlapping set.
Responsibilities:
- Sort proposals by size (prefer larger extractions)
- Eliminate conflicting replacements (shared line ranges)
- Provide deterministic ordering for downstream application
Invariants:
- Filtering is purely structural (no semantic changes to proposals)

## Method Promotion Logic (Within Unification Phase)
When two blocks correspond to methods:
1. Determine method kind (instance/class/static) via decorators.
2. Build ancestry and find nearest shared ancestor class for insertion.
3. Rewrite calls using `MethodCallRewriter` dropping implicit binders (`self` / `cls`).
4. Preserve decorators and dispatch semantics.
Fallback: Module-level insertion if safe ancestor cannot be selected.

## Helper Visitors (Now Top-Level in `visitors.py`)
- `ClassCollector` – Implements phase 3 data gathering.
- `FunctionCollector` – Implements phase 4 function discovery with ancestry context.
- `MethodCallRewriter` – Call-site transformation inside extracted replacements.
- `LoopReturnFinder`, `NameCollector`, `AugAssignFinder`, `AssignTargetVisitor` – Focused analyses used during unification for correctness gates.
- `ClassLocator`, `FuncLocator` – Determine insertion positions and indentation for emitted helpers.

## Orchestration (`run_pipeline`)
```
props = run_pipeline(["src/foo.py", "src/bar.py"], verbose=True)
for p in props:
    # Apply proposal via engine method
    modified = engine.apply_refactoring_multi_file(p)
```
`run_pipeline()` simply wires the phases in fixed order; all heavy logic remains inside the existing engine methods for stability. Future iterations may migrate unification internals into dedicated phase modules while retaining the current external API.

## Invariants & Design Contracts
| Phase | Must Not | Guarantees |
|-------|----------|-----------|
| Parse | Modify source | Valid ASTs or skipped files |
| Scopes | Change AST shape | Accurate binding & free variable sets |
| Class Collect | Modify nodes | Complete `ClassInfo` records including nesting |
| Function Collect | Reorder functions | All functions reachable with ancestry captured |
| Pair Blocks | Mutate function bodies | Candidate pairs store original node references |
| Unify Blocks | Alter original AST in-place | Generated helper & replacements are hygienic and semantics-preserving |
| Overlap Filter | Recompute unification | Non-overlapping, size-prioritized proposal list |

## Testing Strategy
- Unit tests target individual visitors and safety checks.
- Smoke & regression suites exercise the full pipeline implicitly through engine delegation.
- Future: dedicated pipeline-phase tests (e.g., pairing correctness, ancestor selection) can import `pipeline.py` directly.

## Extension Points
- Insert optional normalization phase before pairing (AST canonicalization).
- Introduce scoring phase post-unification to rank proposals by structural density or reuse potential.
- Add caching layer for parse + scope artifacts in long-running daemon mode.

## Rationale for Ordering
Early phases establish semantic context (scopes, classes) required for safe unification; pairing earlier would produce less accurate free variable sets and risk unsafe extraction. Overlap filtering is last to avoid discarding proposals prematurely.

## Backwards Compatibility
Existing calls to `UnificationRefactorEngine.analyze_files()` now internally delegate to `run_pipeline()`—no change required by callers. Direct pipeline usage is optional and intended for advanced integrations and performance experiments.

## Example Minimal End-to-End
```python
from towel.unification.refactor_engine import UnificationRefactorEngine
from towel.unification.pipeline import run_pipeline

paths = ["my_project/module_a.py", "my_project/module_b.py"]
engine = UnificationRefactorEngine(max_parameters=5, min_lines=4)
proposals = run_pipeline(paths, engine=engine, verbose=True)
for proposal in proposals:
    modified_files = engine.apply_refactoring_multi_file(proposal)
    for fpath, content in modified_files.items():
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
```

## Future Work
1. Migrate unification internals into a dedicated `phase_unify.py` for smaller surface area.
2. Add metrics phase for proposal scoring (e.g., lines saved, parameter ratio).
3. Provide interactive selection phase for user-driven curation.
4. Offer incremental mode: apply one proposal, re-run pairing/unification on updated tree.

---
_Last updated: 2025-11-10_
