# Adversarial review — alpha release candidate

The review used executable counterexamples, independent agents, fault injection, consumer projects' own tests, and repeated transformations. Passing evidence establishes the tested cases, not universal equivalence of arbitrary Python programs.

## Confirmed defects repaired

| Counterexample | Repair and executable evidence |
|---|---|
| Moving `self.__value` into another lexical class loses Python's private-name mangling | Reject private-name extraction across lexical classes; preserve same-class extraction. Read-only private properties prevent tests from accidentally accommodating the wrong attribute. |
| `[xs for xs in xs]` resolves its first iterable in the containing scope | Correct comprehension evaluation order and scope isolation; execute list/set/dict/generator cases. |
| Assignment expressions in comprehensions leak bindings to their containing scope | Reject these candidates until their flow is represented; ordinary comprehensions remain useful. |
| Callbacks rebind imported globals or closure cells between reads | Reject snapshots of visibly rebound bindings, including caller parameters captured by nested functions. Preserve tested read-only closures/imports. Detect direct calls to selected namespace-reflection operations; aliased/dynamic reflection and opaque external rebinding remain outside the model. |
| Extracting a loop-body slice moves `break`/`continue` outside its loop | Reject loop transfers without an enclosing loop in the extracted block. Whole internal loops remain eligible. |
| A method receiver is also an explicit generalized operand | Remove only the original receiver parameter position when converting a helper call to method dispatch. Do not remove an arbitrary argument named `self`. Monte Chess's own tests exposed this defect. |
| Hatch `src` discovery emits `src.humanize` instead of `humanize` | Recognize verified Hatch layouts, preserve named setuptools package prefixes, and reject declared unsupported backend configurations. Actual built wheels establish the import names. |
| Rendering a proposal mutates its helper AST/name; call construction aliases input expressions | Deep-copy materialization and call ASTs. Repeated rendering gives the same result and leaves the proposal unchanged. Generated proposals record source digests; immutable byte plans reject stale inputs. |
| Python 3.10 tokenization hides helper names in f-string expressions | Rename resolved AST names using UTF-8 byte positions, preserving surrounding text. Verified historically on 3.10 and 3.13; the release minimum is now 3.11. |
| Global token replacement changes unrelated object attributes or shadowing locals | Resolve lexical module bindings, preserve unrelated names, and reject unsupported class/nested or dynamic cases visibly. |
| A file-qualified rename leaves other modules importing a deleted name | Repair importing modules across the target, preserving their local aliases and re-export names. Test direct/relative imports and module aliases. |
| A later invalid rename or second-file write error leaves earlier edits applied | Stage a complete rename/refactor batch; atomically replace each file; roll back caught failures. Test I/O errors, KeyboardInterrupt, SystemExit, process death, recovery failure, conflicting external edits, and durability faults. |
| A pending transaction in a child directory is missed by a larger parent batch | Inspect every affected path's ancestor journals before starting. Recovery refuses conflicting target bytes. |
| An unrelated ancestor `.git` directory becomes a guessed Python import root | Anchor unconfigured paths at the explicit source directory or classic package ancestry; Git location alone cannot define module names. Test real imports from flat and packaged source trees. |
| CRLF normalization makes unchanged input appear stale | Capture original bytes independently of rendered source; verify execution and on-disk result. |
| Copying directly into a new destination leaves a partial tree after failure | Copy into a private sibling before publishing the completed copy; preserve symlinks without following their targets. |
| Unexpected compiler exceptions become silent candidate rejection | Expected unsupported extraction has a distinct exception; unexpected unifier/extractor/scope failures propagate. Fault-injection tests assert the failure reaches the caller. |

## Additional hostile execution battery

A deterministic 30-case battery exercised every accepted proposal and fixed-point compositions. Cases include mutable aliasing, overloaded arithmetic, augmented assignment, exceptional control flow and `finally`, context managers, early returns, mutable defaults, positional-only and keyword-only arguments, Unicode names, generators, loop transfers, nested callbacks, builtin shadowing, comprehensions, and assignment expressions. Fifteen accepted proposals and fifteen per-case applied transformations preserved the observations; a combined fixture also preserved behavior through three successive transformations. Rejected cases remain unchanged.

The ordinary fixture review separately compiles and executes changed functions and direct methods before accepting a golden output. The generic oracle is sampled and still cannot establish arbitrary external effects, complex construction, or deeper returned-callable behavior. Consumer projects' own tests provide independent evidence for actual transformations.

## Filesystem boundary

Each plan stages all original bytes and replacement images before modifying a target. Replacements are atomic per file, not globally atomic across a batch. Journals support rollback after process death; a completed transaction can leave a cleanup journal without invalidating its committed files. Before rollback, recovery validates every manifest record, original backup, and current target.

Apply and recovery require exclusive write access. An adversarial writer injected exactly between a snapshot check and `os.replace` can still be overwritten: portable check/replace is not compare-and-swap. Advisory locking cannot constrain an unrelated editor. This limit is documented rather than hidden behind a claim of concurrent-writer safety. Read only trusted local recovery journals. File modes and content are tested; the application is intended for ordinary POSIX source files, not arbitrary filesystem metadata or special files.

## Performance and isolation

Candidate blocks/signatures are computed once per function and shared by pairing and clustering during analysis. Exact-gate buckets preserve candidate order. Independent exhaustive enumeration over all 26 fixtures confirms the same candidate pairs. An isolated pairing benchmark measured 3.82× improvement for duplicate-heavy functions and 10.75× for diverse functions; these are pairing measurements, not whole-project speed claims.

Profiling the new rebinding guard exposed repeated module scans consuming 94.3% of one fixture analysis. A frozen analyzer-owned hazard summary reduced that same profiled analysis from 97.446 seconds to 6.153 seconds, preserving all 30 proposals and all 446 checked block decisions. The semantic battery also passes after this optimization.

Every engine owns a bounded analysis session. Cached module/scope/function graphs are private; returned graphs are isolated copies. Content checks defeat same-size/same-mtime edits. Tests cover cache capacity, source-byte budget, working-directory changes, independent engines, invalidation, and mutation poisoning. Each engine and the import-testing harness require sequential use.

Before the rebinding-summary optimization, a large More-itertools module produced thousands of expensive unification candidates and an estimated analysis time above twenty minutes. That exploratory analysis was stopped before writing. The qualifying consumer run refactored a smaller module and ran the consumer's full test suite. Large-project scalability remains a limitation, even with faster candidate generation.

## Final verification

Final interpreter, consumer, artifact, and scan results are recorded in [the readiness report](OPEN_SOURCE_AUDIT.md). Evidence files preserve the commands, counts, source manifests, and original counterexample paths. No claim of production stability or universal preservation is made.

The final concurrent interpreter matrix exposed a test isolation defect: regression copies shared the system temporary parent and therefore its transaction lock. Each stability run now uses a private temporary directory; the runtime correctly rejected the conflicting writer.
