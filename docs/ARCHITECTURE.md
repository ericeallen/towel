# Towel refactoring architecture

Towel finds repeated Python code by unifying statement blocks, extracts each
family of duplicates into one helper function, and rewrites the duplicates as
calls. It is a source-to-source transformation tool. Its design goal is not to
transform as much as possible; it is to transform only where a syntactic,
local argument shows the result is preserved, and to reject everything else.
The checks below are all syntactic and lexical: they do not establish general
behavioral equivalence for programs that observe their own frames, names, or
source, so the intended use is preview, review the diff, and run the project's
own tests.

The engine is `UnificationRefactorEngine` in
[`refactor_engine.py`](../src/towel/unification/refactor_engine.py); it
orchestrates the modules named throughout this document.

## The pipeline

A single analysis runs these stages; the directory driver wraps them in a
fixed-point loop (below).

1. **Parse.** Read source and parse it to an AST, unchanged. Operators and
   assignment forms are preserved; no normalization runs on the analyzed tree.
2. **Scope and binding analysis.** `scope_analyzer.py`, `binding_detector.py`,
   `assignment_analyzer.py`, and `visitors.py` build the module, class, and
   function scopes and record where every name is bound, read, deleted, or
   declared `global`/`nonlocal`.
3. **Enumerate candidate blocks.** Contiguous runs of statements inside each
   function body become candidate blocks. Blocks that can never be accepted are
   never enumerated (see *Enumeration filter*).
4. **Filter pairs.** `block_signature.py` computes a cheap structural signature
   per block and rejects incompatible pairs before the expensive step.
5. **Unify.** `unifier.py` (with `nominal_unifier.py`) attempts to anti-unify
   each surviving pair into a template plus a substitution (below).
6. **Guard.** `semantic_safety.py`, `orphan_detector.py`, and
   `definite_assignment.py` reject a candidate whose extraction could change
   behavior (frames, closures, liveness, import cycles; see *Guards*).
7. **Verify.** The proposed helper is instantiated with each call site's
   actual arguments and compared against the block it would replace, up to
   renamed binders (see *The soundness invariant*). This gates every proposal.
8. **Cluster.** `refactor_engine.py` searches the rest of the file for further
   blocks that unify with the accepted template and can share the helper.
9. **Materialize.** `extractor.py` renders the helper and the call sites,
   compiles the generated Python to confirm it parses, and detects overlapping
   replacements.
10. **Apply.** `changes.py` turns accepted proposals into an immutable byte
    plan and applies it transactionally (see *Application and recovery*).

`models.py` defines the data that flows between stages: parsed modules,
function artifacts, class info, code-block pairs, substitutions, replacements,
and proposals.

## The core algorithm: anti-unification into a helper

Two blocks unify when they have the same statement shape and differ only in
sub-expressions that can be abstracted into parameters. This is *anti-unification*,
also called least general generalization [Plotkin 1970; Reynolds 1970]:
the template is the most specific pattern both blocks instantiate. The unifier walks both
ASTs in lockstep. Where the trees agree it keeps the structure; where they
differ it introduces a parameter, provided the differing nodes are expressions
that can be abstracted safely. Binder names (loop variables, comprehension
targets, `with`/`except` names) are matched up to consistent renaming rather
than turned into parameters.

Each parameter is passed in the way that preserves the original evaluation:

- **Value.** A name, literal, or tuple of those is passed eagerly. It has no
  observable effect and no fresh identity, so evaluating it at the call site is
  indistinguishable from evaluating it in place.
- **Thunk.** Any other expression is passed as a zero-argument lambda (a *thunk*
  [Ingerman 1961]) and called inside the helper exactly where the original
  expression stood. This
  preserves evaluation order, count, and conditionality: an expression the
  original evaluated twice, or not at all on some path, is evaluated the same
  number of times under the same conditions. As an optimization, a thunk the
  helper would evaluate first, once, and unconditionally is passed eagerly
  instead, because nothing can observe the difference.
- **Lifted.** An expression that reads a name bound *inside* the block is
  lambda-lifted [Johnsson 1985]: the lambda takes those names as arguments so it still refers
  to the block-local values, not to whatever the helper's scope binds.
- **Receiver.** When the helper becomes a method, the instance or class is
  passed as the receiver (see *Helper placement*).

The result is a `Substitution` (in `models.py`): the template, the ordered
parameters, and, per parameter, the argument expression at each call site and
its kind. `unifier.py` refuses to parameterize a node that is not an
`ast.expr` (a slice, a starred item, a whole f-string), because those are
container syntax, not values.

## The soundness invariant

Acceptance never trusts the algorithm; it checks the result.
`instantiation.py` takes the generated helper, substitutes each call site's
actual arguments back into the helper body, alpha-normalizes both it and the
original block (binders renamed to positional placeholders, i.e. compared up to
alpha-equivalence [Church 1936; Barendregt 1984], annotations
replaced by a placeholder because they are inert at runtime), and requires the
two to be structurally identical. A proposal is offered only if this holds for
**every** call site. This is the property that makes the transformation safe to
apply after review: the helper, called as written, reduces to the exact code it
replaced. A mismatch — from a subtle scoping or parameterization error — drops
the proposal rather than emitting it.

## Scope, binding, and liveness

Extraction must not orphan a name. A forward dataflow *definite-assignment*
analysis [Gosling et al., Java Language Specification ch. 16; cf. Nielson,
Nielson & Hankin 1999] answers which names are guaranteed bound at a point.
`definite_assignment.py` provides
`definitely_bound_after(statements)`: the set of names guaranteed bound after a
run of statements, or `None` if control cannot fall through (every path
returns, raises, breaks, or continues). `orphan_detector.py` uses it
path-sensitively: for each statement remaining after the extracted block, it
subtracts the names *definitely* bound on the way to that statement from the
names the block bound and the remaining code reads. A name the block binds and
later code reads is safe to move into the helper only when every path from the
block's end to that read rebinds it first; a name rebound on only some paths is
treated as orphaned and the block is rejected. `extractor.py`'s
`has_complete_return_coverage` similarly requires a value-producing block to
return on every path before it may be called as `return helper(...)`, using
both the rendered shape and the all-paths-exit property.

## Guards

`semantic_safety.py` rejects a block, before verification, when moving it into
a helper could change behavior even if the shapes match:

- **Frames and suspension.** `yield`, `await`, `async for`/`with`, `locals()`,
  `globals()`, no-argument `vars()`/`super()`, `eval`/`exec`, direct frame or
  stack inspection, and `warnings.warn(..., stacklevel=...)` — a helper adds a
  frame these would observe.
- **Binding discipline.** A block that deletes, rebinds, or `except ... as`
  binds a name the caller keeps using; a moved `global`/`nonlocal`
  declaration; a comprehension assignment expression that would bind in the
  wrong scope.
- **Closures.** A nested function or lambda in the block that shares a rebound
  name with it, or that would capture a different cell after extraction.
- **Import cycles.** A cross-file helper whose new import would close a static
  import cycle (see *Cross-file*).

Only constructs written directly in the block are caught here; frame use
reached through a callee is handled by the pre-run scan (below), and reflection
reached through aliases is outside the model.

## Helper placement

A helper is inserted where every call site can see it. `refactor_engine.py`
decides:

- **Method.** When both blocks are methods of one unique module-level class,
  or of classes with a unique module-level common ancestor, every decorator is
  known to preserve the receiver, and the first parameter is `self` (or the
  method is a `classmethod`), the helper becomes a method and the receiver is
  passed explicitly.
- **Common ancestor by import resolution.** A base-class name is resolved the
  way the referencing module resolves it: a class of that qualname in the same
  module, else the class in the module named by one of that module's own
  unconditional imports (relative, absolute, aliased, or dotted). A name is
  never matched across the project, so a project with several same-named base
  classes (a `BaseEndpoint` per protocol) does not misattribute the ancestor.
- **Module level otherwise.** When the blocks are in local classes, nested
  functions, or functions whose common enclosing function name is not unique in
  the file, the helper is hoisted to module level and takes everything as
  parameters. Every free variable is already a parameter, so a module-level
  helper is always a correct fallback, and it avoids placing a helper in a
  scope that a same-named sibling function cannot see.

## Cross-file behavior

`project_layout.py` maps files to importable module names so a cross-file
helper can be imported correctly. It reads the build backend from
`pyproject.toml` and derives import roots for setuptools (including
`package-dir` mappings and the classic `src` layout), Hatch (wheel
`packages`/`sources`), Flit, Poetry (`packages` with `from`), and pdm
(`package-dir`). An unrecognized backend falls back to conventional inference:
a package or module named after the distribution, in the project root or under
`src`. Only a layout that cannot be resolved either way raises rather than
guessing; the ecosystem check reports those as `UNSUPPORTED`. `semantic_safety.py`'s
`would_create_import_cycle` follows static imports through local modules,
caching each module's import edges by path, mtime, and size, and rejects a
helper placement that would close a cycle.

## The clustering pass

Once a pair is accepted and its helper template fixed, `refactor_engine.py`
scans the rest of the file for additional blocks that unify with the template
and can call the same helper. A clustered site joins only when it passes the
same guards and, for a method helper, is a method of the same class with the
same receiver kind; a site whose scope cannot see the helper is skipped. The
per-candidate pipeline is memoized on the template, the candidate, and the
helper.

## Pre-run frame- and source-sensitivity scan

Because a callee's frame use is invisible to the block-level guard, directory
mode scans every module before refactoring and prints a stderr diagnostic
naming files that inspect call frames or tracebacks, attribute warnings by
`stacklevel`, or read source through `inspect.getsource`
(`frame_sensitivity_markers` in `semantic_safety.py`). It is a warning to
review those diffs or `--exclude` them, not a refusal; it deliberately does not
flag patterns indistinguishable from safe code, such as reading a sibling's
`.py` source through a plain `open`. See
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for what it does and does not
catch.

## The fixed-point loop

`refactor_directory_to_fixed_point` performs whole-project analysis, applies
one proposal, re-analyzes the affected source, and repeats until no proposal
remains or an iteration bound is reached. Applying one change at a time keeps
each step verifiable and lets a later iteration extract a helper that a newly
introduced call site now shares. The single-file `refactor_to_fixed_point` is
the same loop over one module.

## Performance architecture

Analysis is quadratic in candidate blocks per file, so the engine spends its
effort avoiding and reusing comparisons. Every measure is exact and changes no
proposal.

- **Enumeration filter.** A block that returns on some path but not all, or a
  lone expression statement, can never be accepted and is never enumerated.
- **Structural identity.** `_sid` is the SHA-256 of `ast.dump(block)`. All the
  engine's caches — guard verdicts, unification results, the clustering
  pipeline, per-block analyses — are keyed by structural id, so a fixed-point
  iteration that re-parses a file still reuses results for the blocks it did not
  change. Caches are bounded LRU `OrderedDict`s (`STRUCTURAL_CACHE_LIMIT`,
  250,000 entries). `structural_memo.py` stores a unification result as
  positions and rehydrates it onto the matching re-parsed block.
- **Shared analysis graphs.** Each engine owns an `AnalysisSession`
  ([`pipeline.py`](../src/towel/unification/pipeline.py)) that parses and
  analyzes each module once and returns the *same* graph on every access,
  keyed by path and current content (LRU, 128 entries / 8 MiB of source). The
  graph is shared by reference, not copied, because the analysis treats it as
  read-only; `TOWEL_CHECK_AST_IMMUTABLE=1` verifies that on every reuse by
  comparing an AST digest and raising if the tree changed. A session is owned
  by one caller and is not thread-safe.
- **Import-edge cache.** The import-cycle check parses each reachable module
  once per analysis and caches its edges, instead of re-parsing per pair.
- **Fork-based parallelism.** A large cold analysis forks worker processes
  after parsing; each worker inherits the ASTs and caches copy-on-write and
  returns only accepted proposals, so nothing is pickled in and only results
  travel back. Forking is decided by a timed serial probe over a strided sample
  of all pairs — never by pair count alone, since a worker pool per fixed-point
  iteration can cost more than a small iteration saves. Each worker runs a
  watchdog thread that ends it within a second of its parent's death, so a
  killed run leaves nothing behind; the worker count is also capped by the
  parent's resident size against physical memory. `TOWEL_WORKERS=1` disables
  forking; any other value caps the count. See
  [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md#resources-and-platform) for
  measured memory and time.

## Application and recovery

`changes.py` applies an immutable byte plan. It stages every backup and
replacement byte before touching a target, checks each file's current contents
against what the plan was built on, replaces each file atomically, and rolls
back caught failures. A durable journal under `.towel-transaction-active`
records the original bytes so `towel recover` can restore an interrupted batch;
recovery refuses detected conflicting edits and keeps the journal for
resolution. Out-of-place refactoring stages the initial copy separately so a
copy error leaves no partial output. A batch is atomic per file, not globally
atomic to a concurrent reader, and apply/recover need exclusive write access:
snapshot checks detect a racing writer but cannot prevent one. See
[SECURITY.md](../SECURITY.md).

## Helper naming

Extraction generates deliberately meaningless names (`__extracted_func_3`,
`__param_0`); naming is a separate, review-first step. `towel rename-helpers
--list --json` emits an inventory of every helper with its scope, source, call
sites, and per-parameter evaluation kind and argument expressions — the
material a coding assistant reads to choose names. `--rename-file` applies a
mapping as one atomic batch with scope and importer checks; a collision, a
mangled name, or a dynamic reference aborts the whole batch and reports why, so
a bad suggestion changes nothing. See the README for the end-to-end workflow.

## Verification and evidence

The transformation's safety rests on the per-proposal instantiation invariant.
The evidence that the engine holds up on real code is layered:

- **Unit and integration tests** cover scope analysis, extraction, the guards,
  and real CLI runs, under strict AST snapshots.
- **Hostile batteries** (`tests/hostile_cases`, `tests/hostile_crossfile`)
  execute adversarial fixtures before and after fixed-point refactoring and
  assert identical program output; each fixed engine defect is a fixture and a
  row in [ADVERSARIAL_REVIEW.md](ADVERSARIAL_REVIEW.md).
- **The standing ecosystem check** (`scripts/ecosystem_check.py`, `just
  ecosystem`, weekly in CI) clones public projects, runs each one's own suite,
  refactors a copy, and runs the suite again, comparing test outcomes; it is
  the evidence in [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

Behavioral comparison samples finite inputs and does not prove equivalence;
unsupported callable shapes are reported as unverified. CI enforces an 85%
coverage floor across Python 3.11–3.13. Coverage measures executed code, not
correctness. See [CONTRIBUTING.md](../CONTRIBUTING.md) and
[RELEASING.md](RELEASING.md).

## References

The engine composes several standard results; the implementations are original,
but the ideas and their names are from the literature.

- **Anti-unification / least general generalization.** Plotkin, G. D. (1970).
  "A Note on Inductive Generalization." *Machine Intelligence* 5, 153–163.
  Reynolds, J. C. (1970). "Transformational Systems and the Algebraic Structure
  of Atomic Formulas." *Machine Intelligence* 5, 135–151.
- **Lambda lifting.** Johnsson, T. (1985). "Lambda Lifting: Transforming
  Programs to Recursive Equations." *Functional Programming Languages and
  Computer Architecture*, Springer LNCS 201, 190–203.
- **Thunks (delayed evaluation).** Ingerman, P. Z. (1961). "Thunks: A Way of
  Compiling Procedure Statements with Some Comments on Procedure Declarations."
  *Communications of the ACM* 4(1), 55–58.
- **Alpha-equivalence.** Church, A. (1936). "An Unsolvable Problem of Elementary
  Number Theory." *American Journal of Mathematics* 58(2), 345–363. Barendregt,
  H. P. (1984). *The Lambda Calculus: Its Syntax and Semantics.* North-Holland.
- **Definite-assignment / dataflow analysis.** Gosling, J., Joy, B., Steele, G.,
  Bracha, G., Buckley, A. *The Java Language Specification*, ch. 16 "Definite
  Assignment." Nielson, F., Nielson, H. R., Hankin, C. (1999). *Principles of
  Program Analysis.* Springer.

## Module map

| Concern | Modules |
|---|---|
| Orchestration, clustering, placement | `refactor_engine.py` |
| Parse/analyze cache | `pipeline.py` |
| Anti-unification | `unifier.py`, `nominal_unifier.py` |
| Pair pre-filter | `block_signature.py` |
| Verification | `instantiation.py` |
| Scope and bindings | `scope_analyzer.py`, `binding_detector.py`, `assignment_analyzer.py`, `visitors.py` |
| Liveness and orphans | `definite_assignment.py`, `orphan_detector.py` |
| Safety guards, import cycles, pre-scan | `semantic_safety.py` |
| Helper and call-site rendering | `extractor.py`, `thunk_inlining.py` |
| Structural memoization | `structural_memo.py` |
| Cross-file layout | `project_layout.py` |
| Data model | `models.py` |
| Transactional application | `changes.py` (at `src/towel/`) |
