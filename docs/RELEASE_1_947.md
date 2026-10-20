# Towel 1.947: audit repairs after 1.946

[Documentation index](README.md) · [Changelog](../CHANGELOG.md#1947)

The maintainer selected **1.947** for the repairs below. This overview describes
the versioned source; final validation and publication outcomes are recorded
externally with the artifacts and on [PyPI](https://pypi.org/project/code-towel/).

**Preparation checkpoint, October 19, 2026:** 1.947 was unpublished, and 1.946
was the latest confirmed release. Its published artifacts cannot be replaced.
The final 1.947 source and distributions still required their own complete
release validation at this checkpoint.

## Binding, extraction and formatting repairs

The repairs follow the audit's five priorities and address seven confirmed
findings:

1. **Python binding identity.** Parameter renaming respects class-private name
   mangling in nested classes and methods. Helper renaming matches Unicode
   identifiers by Python's normalized identity while editing their original
   source spans. Definitions and their resolved references change together;
   unsafe batches refuse before writing.
2. **Candidate enumeration.** Functions without an eligible structural partner
   skip candidate-block enumeration. Eligible intervals reuse per-suite prefix
   facts instead of repeatedly scanning the same statements. Pair budgets and
   ordering remain in force. These changes address measured analysis costs;
   they do not establish a new complete-command performance claim for 1.947.
3. **Definition-time lookup mappings.** When a module's `__builtins__` may be replaced,
   extraction declines explicit module/global fallback reads, including custom
   mapping keys and currently module-bound names, plus implicit import and class
   construction lookups. `--parameterize-builtins` cannot override this rule.
   Explicit locals, parameters and cells, `__debug__`, and mapping-independent
   blocks retain their ordinary rules. The guard follows the actual evaluation
   scopes, including defaults and eager annotations, with conservative coverage
   for inlined comprehensions. Wildcard exports can replace `__builtins__` too.
   An ordinary `receiver.__builtins__` store/deletion anywhere in the analyzed
   program makes every module uncertain, irrespective of receiver aliases;
   excluded runnable files still contribute to that check.
   See the [captured-mapping policy](GENERATED_CODE.md#arguments-and-evaluation).
   A bounded source-only verdict memo avoids repeated large-module lexical
   scans for warning-free inputs while retaining native warning behavior and
   fresh AST/block proofs. See the [performance limits](PERFORMANCE.md#1947-large-module-builtin-guard-repair).
4. **Each file's formatting policy.** Generated helpers, declarations and calls
   use their actual destination file's configuration. Ruff import sorting
   recognizes standalone, dotfile, extended and nested configurations through
   native effective settings. Disabled rules, per-file ignores and exclusions
   remain in force. Transient settings-query failures are reported and retried
   on later requests instead of being cached as a disabled policy.
5. **Rename consumer scope.** Definition selection is separate from the project
   scope needed to resolve ordinary consumers. A batch needing edits outside
   the selected target refuses atomically and explains how to rerun at the
   project root. Towel does not silently change files outside that target.

The integrated source also retains the annotation-import placement and corpus
editable-build discovery repairs from the separate 1.946 corpus branch.

## Rename scope is now explicit

A narrower target can now refuse where an earlier invocation reported success
while leaving an ordinary consumer broken. Select the complete project root
and use a path-qualified mapping key to choose a specific definition. For an
unmarked project whose complete scope Towel cannot establish, the CLI requires
an explicit `--complete-scope` declaration. That declaration cannot bypass a
known outside consumer. External installed consumers remain
outside what a local project scan can prove. See the [naming guide](NAMING.md).

This release preserves the existing behavioral contract: ordinary binding,
evaluation order, effects and interfaces remain covered. Reflection and
self-instrumentation remain outside that guarantee. The repairs do not restore
project-wide reflection scans or broaden that exclusion. See
[known limitations](KNOWN_LIMITATIONS.md).

## Prior repair evidence and remaining release gates

The later frozen candidate `59aafb1` completed its Linux Python 3.11–3.13
matrix, 9,400 differential runs with zero failures, six clean-install smoke
checks, 126 sdist documentation checks and self-dogfood checks. Its corpus was
intentionally stopped after eight completed project results when More-itertools
exposed repeated lexical scans by the builtin-mapping guard. The published
1.946 wheel completed the same case with byte-identical Python output. The
source-only verdict reuse above addresses that repeated-input path; neither
these prior-source results nor the partial diagnostic profiles validate the
subsequent repair or establish its performance. See the
[source-bound checkpoint](PRODUCTION_READINESS.md#1947-candidate-59aafb1-corpus-stop)
for exact counts and limits. The changed runtime requires its own complete
release gates, with final outcomes recorded externally.

The preceding repair source, `2027689`, still carried **1.946 metadata**. Its
Linux Python 3.13 `just ci` run passed 10,901 tests and 45 subtests, skipped
226 tests, measured 94% line coverage and completed the declared quality,
dependency-audit and distribution-build gates. Those results belong to that
source; the version and documentation changes for 1.947 require a fresh freeze.
The [readiness report](PRODUCTION_READINESS.md) records the additional scoped
native, wheel and differential checks without treating them as final-release
evidence.

At the October 19 preparation checkpoint, the final 1.947 Python 3.11–3.13
full-suite/coverage matrix, complete corpus,
consumer/performance work required by the [release procedure](RELEASING.md),
exact distributions and evidence verification remained pending. Exact-commit
remote CI, the release tag, its documentation URLs and publication also remained
pending. The standing prohibition on Git pushes blocked an agent from
completing those remote steps. Preparation and local validation do not bypass
the release procedure or authorize an upload.
