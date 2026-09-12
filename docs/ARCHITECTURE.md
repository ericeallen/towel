# Towel refactoring architecture

Towel parses Python source, compares statement blocks through AST unification, and proposes extracted helpers with replacement call sites. It is an experimental source transformation tool: syntactic checks and conservative scope guards reduce risk, but do not establish general behavioral equivalence. Review generated diffs and run the affected project's own tests.

## Analysis and extraction

The main API is `UnificationRefactorEngine` in [`refactor_engine.py`](../src/towel/unification/refactor_engine.py). It coordinates these stages:

1. Read and parse source without changing operator or mutation semantics.
2. Build module, class, and function scope information with `scope_analyzer.py`, `binding_detector.py`, and `visitors.py`.
3. Enumerate candidate statement blocks and use `block_signature.py` to reject structurally incompatible pairs before unification.
4. Use `unifier.py` and `nominal_unifier.py` to compare AST structure and binding roles. `orphan_detector.py` and `assignment_analyzer.py` constrain extraction according to available bindings and later reads.
5. Generate helpers and call sites through `extractor.py`; detect overlapping replacements and rank proposals.
6. Materialize proposals, compile generated Python, and apply the selected changes. Fixed-point entry points reanalyze affected source between applications and stop when no proposal remains or the iteration bound is reached.

The engine exposes `analyze_file`, `analyze_files`, and `analyze_directory` for analysis. A typical constructor is:

```python
from towel.unification.refactor_engine import UnificationRefactorEngine

engine = UnificationRefactorEngine(min_lines=4, max_parameters=5)
proposals = engine.analyze_file("example.py")
```

`models.py` defines parsed modules, function artifacts, replacement sites, and proposals. Proposals carry source paths and replacement ranges; human-readable descriptions are explanatory text, not authoritative metadata for selecting affected code.

## Ownership and caching

Each engine owns an `AnalysisSession` from [`pipeline.py`](../src/towel/unification/pipeline.py). It checks current source content on every access and caches consistent module/scope/function graphs. Returned graphs are independent copies so downstream mutation cannot corrupt a retained analysis snapshot. The default limits are 128 entries and 8 MiB of retained source; the byte limit does not measure the larger Python object graph. Sessions are owned by one caller and are not shared between threads.

The engine also memoizes assignment and name analysis using weak AST keys. Reuse an engine within one analysis workflow; do not assume an instance is safe for concurrent mutation.

## Legacy normalization utilities

[`ast_normalizer.py`](../src/towel/unification/ast_normalizer.py) remains an importable compatibility module and emits `DeprecationWarning` when its transformers are constructed. Wrapper functions return an independent AST; direct `NodeTransformer` visitors retain their conventional mutating behavior. No removal version is scheduled. Its assignment-to-augmented-assignment and arithmetic rewrites are **not used by the production pipeline**. They are unsafe for arbitrary Python: `x = x + y` and `x += y` can differ in aliasing and operator dispatch, while reordered expressions can change evaluation order or overloaded behavior. Do not add those passes back to source analysis or use them as an equivalence oracle.

## Cross-file behavior and safety boundaries

`project_layout.py` supplies package/module context for imports. Cross-file proposals share a helper across replacement sites and must preserve the bindings required at each site. Hatch's conventional classic-package layout and explicit wheel `packages`, along with setuptools package-directory mappings, supply verified source-root information. Unsupported declared backends, unmatched Hatch defaults, and custom Hatch source rewrites raise an error instead of guessing a module name. Unconfigured trees retain the legacy path-based fallback. Python's dynamic import behavior, reflection, descriptors, decorators, callbacks, and external effects remain beyond a general static proof.

Analysis parses target source without executing it. Behavioral tests execute source and belong only in trusted test environments. Directory discovery excludes symlinked Python files and checks output-tree overlap. Compilation establishes syntactic validity, not behavioral preservation. `changes.py` applies immutable byte plans after checking the original contents. It stages backups and replacement bytes before replacing any target, replaces each file atomically, and rolls back caught failures. A durable journal supports `towel recover` after interruption. A batch is not globally atomic to concurrent readers; separate extraction iterations are separate batches. Apply and recovery require exclusive write access: snapshot checks can detect conflicts but cannot eliminate a race with an unrelated writer. Consult [SECURITY.md](../SECURITY.md) for execution and reporting policy.

## Performance and verification

Candidate discovery and unification dominate large analyses. Structural filtering avoids many comparisons, but no universal rejection percentage or runtime bound is promised. Similar functions can produce many block pairs, and retaining candidates costs memory. Progress output should describe actual work rather than simulate completed comparisons.

The test suite combines focused scope/extraction regressions, real CLI calls, strict AST snapshots, and observational comparisons. Runtime comparisons include return values, output streams, argument mutation, and supported method state. A finite input sample does not prove equivalence; unsupported callable shapes must be reported as unverified. Consumer validation should run the consumer project's own tests both before and after actual transformations, with pinned revisions and unchanged originals.

CI enforces an overall 85% coverage floor across Python 3.10–3.13. Coverage measures executed code, not semantic correctness. See [CONTRIBUTING.md](../CONTRIBUTING.md) for local checks and [RELEASING.md](RELEASING.md) for publication evidence.
