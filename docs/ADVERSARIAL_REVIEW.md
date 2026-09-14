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

## Second review — production readiness, September 13, 2026

A second round of executable counterexamples, followed by seven public
consumer projects run under the CLI defaults and then the 39-project
ecosystem check (`scripts/ecosystem_check.py`), found the defects below. Each
is repaired and covered by a fixture in `tests/hostile_cases` or
`tests/hostile_crossfile`, which execute before and after fixed-point
refactoring and assert identical program output.

| Counterexample | Repair |
|---|---|
| `k in d` unified as equal while `d[k]` and `d[k + 1]` were parameterized; the template substituted the parameter at both positions | Every replacement is verified by instantiating the helper with the call's arguments and comparing against the original block up to renamed binders. Disagreement between unification, substitution, and renaming now rejects the proposal. |
| `self.email` versus `self.phone` evaluated once at the call site instead of at each read; properties with effects and conditional reads reordered | Only names, literals, and containers of those are hoisted. Other expressions become zero-argument thunks evaluated inside the helper at the original position. |
| A block assigned `factor`, which a closure defined earlier read through a cell; the helper assigned its own local | Reject blocks that rebind a name read by any nested scope outside the block, and blocks whose own closures read a name the caller rebinds afterwards. |
| `del x` and `except ... as e` unbound a helper local; the caller's variable survived | Reject deletion, explicit or implicit, of a name bound before the block or declared global/nonlocal. |
| `match` capture names were unknown to scope analysis and passed as undefined arguments | Match patterns bind in the scope analyzers and the binding collector. |
| A block beginning with `global counter` took the declaration into the helper; the caller's later `counter += 1` became a local write | Reject blocks whose scope declarations are still referenced outside them. |
| `seq[start:stop]` versus `seq[i]` parameterized a slice and rendered `lambda: start:stop` | Slices and starred items are never parameterized. |
| A `_lazyclassproperty`-decorated method received `cls`; the helper was emitted as an instance method and called through the class with one argument too few (pyparsing) | Receiver dispatch is used only when every decorator is known to preserve the receiver; otherwise the helper is module-level with the receiver passed explicitly. Materialization also verifies every generated call binds to the helper's signature. |
| Clustering added a replacement nested inside a block already replaced; the write failed with overlapping ranges (toolz) | Cluster candidates may not intersect any covered range. |
| On a second fixed-point pass, a fresh `__param_0` collided with a helper parameter of that name; call generation raised IndexError (boltons) | Generated parameter names skip every identifier the blocks mention; duplicate signatures are refused. |
| Flit-packaged projects were refused in directory mode (boltons, markdown-it-py) | Flit's single module is located from `[tool.flit.module]` or the project name, beside `pyproject.toml` or under `src`. |
| Each block's live variables were an independent set, so a second call unpacked the helper's tuple in a different order or under other names that alpha-renaming had unified (boltons: 53 URL tests, traceback frames) | The union of live variables is ordered by the template's names and mapped to each block through the renames; the instantiation check compares assignment targets with the helper's return. |
| A block that returned early and also bound live variables was rendered as an assignment, so `return self` became `cls = self` (boltons cachedmethod) | Such blocks are rejected; the instantiation check refuses an early return alongside returned variables and requires a `return` call for helpers that return early. |
| A docstring line beginning with `import` placed the cross-module import inside the docstring (boltons tbutils) | Import position comes from the parsed module: after the docstring and leading imports. |
| Two modules each defined `_extracted_func_4` because counters were seeded only from files touched by one proposal (boltons dictutils/fileutils) | Counters consider every file in the analysis, so helper names are unique across the project. |
| A list or set display was classed as pure and hoisted eagerly, so `acc.append([])` inside a loop appended one shared list every iteration | Only tuples of names and literals are eager; every other display is a thunk evaluated in place. |
| An assignment expression inside a parameterized expression would bind its target in the thunk's scope rather than the caller's | Expressions containing a walrus are never parameterized. |
| Two local classes with the same name were treated as one, so a method helper landed in the wrong class (cachetools) | Method insertion requires a unique module-level class; materialization fails rather than silently falling back to module level. |
| A tab-indented class received a space-indented method, and the file no longer compiled (pathspec) | Helpers take the class body's own indentation character and unit. |
| A free variable bound only on some path before the block, and read only on some path inside it, was read eagerly at the call and raised `UnboundLocalError` (glom `err`) | A definite-assignment analysis passes locals that are not certainly bound as thunks. |
| The same for a parameter whose argument was a bare local name bound only inside a `try` body (glom `cur`/`ret`) | Uncertain name arguments go through the same definite-assignment rule as free variables. |
| A direct `warnings.warn(stacklevel=...)` inside the block reported the helper's caller one frame off (pluggy) | Direct `warnings.warn` with `stacklevel` and frame-inspection calls are rejected; pluggy's indirect case is the documented `BROKEN_KNOWN` in the ecosystem check. |
| A queued proposal whose file an earlier application had changed aborted the whole batch with "Stale proposal" (pygments, pyflakes) | The fixed-point loop drops the stale proposal, invalidates its files, and re-analyzes them; the post-application filter considers every participating file. |
| A function nested inside a method was treated as a method of the lexical class: its first parameter became the receiver, so `decorator(f)` produced `f._extracted_func_6(...)` (click) | A function is a method only when the scope analyzer places it directly in a class body. A nested function keeps the class for name mangling but gets no receiver, and a helper without a method context stays at module level. |
| An annotated assignment with a value, `captured_style: Optional[X] = None`, was not counted as a binding, so the helper returned only the other variable and the caller read it unbound (wcwidth) | Annotated assignments and assignment expressions bind their targets in both the reassignment classifier and the block binding collector, so they take part in live-variable returns. |
| A block that began at an `elif` was extracted and its call rendered as a sibling statement after the outer `if`, so the branch ran unconditionally; the generated template compiler emitted unbalanced parentheses (jinja2). The nested_structures golden had recorded the same misplacement, preserved only by mutually exclusive type tests. | An `elif` is never a block start; its body and its own branches remain candidates. The golden was regenerated. |
| Analyzing one 2,167-line test module ran for more than nine minutes: every safety guard re-walked the whole enclosing function for each of 444,250 candidate pairs (pyflakes) | Block guards are memoized per (guard, function, block), since a block takes part in every pair it forms. |
| A variable bound only inside a branch that raises, `if x is None: msg = ...; raise ValueError(msg)`, was returned by the helper, which reached `return msg` unbound on the path where the original fell through (platformdirs) | A helper may return a variable only when it is definitely bound at the block's exit or entered as a parameter; otherwise the proposal is rejected. |
| A helper that became a method of one test class was also called, through `self`, from identical blocks in sibling classes that the clustering pass had gathered from the same file (pyflakes) | When the pair's blocks sit in classes, a cluster candidate must be a method of one of those classes with the same receiver kind; blocks elsewhere in the file keep their code. |
| `fstring_rules(ttype)` is defined in a lexer class body and called while that body runs; its first parameter was taken for the receiver, so the helper became a method of the common base class and the call read `ttype._extracted_func_1(String)` on a token object (pygments) | An instance method whose first parameter is not named `self` has an unknown receiver: its helper stays at module level and the parameter is passed explicitly. |
| `gen_rubystrings_rules()` takes no parameters and runs while its lexer class body executes; the missing first parameter defaulted to `self`, so the helper became a method of the base lexer and the call read `self._extracted_func_3(...)` where no `self` exists (pygments) | A class-body function with no positional parameter has nothing to dispatch on; its helper stays at module level. |
| Two blocks differing only in `from jsonschema import X` were unified: an import alias is not an expression, but a differing primitive field parameterized the whole alias node, and the instantiation check alpha-renamed the imported name as if it were a binder, so a second fixed-point pass built a helper importing the template's name for every caller (jsonschema) | Only expressions may become parameters; an import's name is renamable only through `as`, in both unification and the instantiation check. |
| An import inside a block binds a name the caller reads afterwards, and the binding collectors did not count imports, so the helper did not return it (jsonschema `Validator`) | Imports bind their aliases, or the first component of a dotted name, in both the reassignment classifier and the block collector. |
| `self.body: list[NodeNG] = []` inside a method: `NodeNG` is imported only under `TYPE_CHECKING`, yet it was passed to the helper as a free variable, so the call raised `NameError` where the original never evaluated the annotation (astroid) | Inside a function body no annotation is evaluated, whatever the target; annotations contribute no free variables, are neither compared nor parameterized by unification, and are ignored by the instantiation check. |
| `frame, stmts = self.lookup(self.name)` inside a block: the binding collector recorded only bare-name targets, so the unpacked names were not returned and the caller read `frame` unbound (astroid) | Assignment and `with` targets bind every name inside a tuple, list, or star. |
| Two nested functions of one outer function shared a block, so the helper was inserted into that outer function; a third occurrence in a module-level function of the same file was clustered onto it and called a name that does not exist at module level (prompt_toolkit) | When the helper's home is the pair's deepest common enclosing function, clustered call sites must lie inside that function. |

Cross-file fixtures confirm that a block reading a module-level function,
class, import alias, or `__file__` with a different meaning in each module
receives that object from the caller rather than resolving it in the helper's
module.
